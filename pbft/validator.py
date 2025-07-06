import json
import requests
import time
import threading
from typing import Dict, List, Optional, Tuple
from django.conf import settings
from image_app.models import Block, BlockchainState
from concurrent.futures import ThreadPoolExecutor, as_completed
from .consensus import PBFTConsensus, PBFTState

class PBFTValidator:
    """
    PBFT Validator handles the node's role in the PBFT consensus process.
    It manages communication with other nodes and validates transactions.
    """
    
    def __init__(self, node_id: str = None, node_address: str = None, port: int = None, node_list: List[dict] = None):
        """
        Initialize the PBFT validator.
        
        Args:
            node_id: Unique identifier for this node
            node_address: IP address of this node
            port: Port this node listens on
            node_list: List of all nodes in the network
        """
        from django.conf import settings
        
        # Use Django settings if parameters not provided
        self.node_id = node_id or getattr(settings, 'NODE_ID', 'node_1')
        self.node_address = node_address or '127.0.0.1'
        self.port = int(port) if port is not None else int(getattr(settings, 'NODE_PORT', 8000))
        self.node_url = f"http://{self.node_address}:{self.port}"
        
        # Initialize node information
        self.nodes = {}
        
        # Try to get nodes from settings first
        if hasattr(settings, 'PBFT_NODES'):
            for nid, node_url in settings.PBFT_NODES.items():
                if nid != self.node_id:  # Exclude self
                    self.nodes[nid] = {
                        'url': node_url,
                        'active': True,
                        'last_seen': time.time()
                    }
        # Fall back to node_list if provided
        elif node_list:
            for node in node_list:
                if node['id'] != self.node_id:  # Exclude self
                    self.nodes[node['id']] = {
                        'url': f"http://{node['address']}:{node['port']}",
                        'active': True,
                        'last_seen': time.time()
                    }
        else:
            # Default configuration if nothing else is available
            default_nodes = {
                'node_1': 'http://127.0.0.1:8000',
                'node_2': 'http://127.0.0.1:8001',
                'node_3': 'http://127.0.0.1:8002',
                'node_4': 'http://127.0.0.1:8003'
            }
            for nid, node_url in default_nodes.items():
                if nid != self.node_id:
                    self.nodes[nid] = {
                        'url': node_url,
                        'active': True,
                        'last_seen': time.time()
                    }
        
        # Initialize consensus
        total_nodes = len(self.nodes) + 1  # Include self in total nodes
        self.consensus = PBFTConsensus(self.node_id, total_nodes)
        self.state = PBFTState.REPLY
        self.pending_transactions = []
        self.executor = ThreadPoolExecutor(max_workers=10)
        
        # State for collecting messages
        self.pre_prepare_log: Dict[Tuple[int, int], Dict] = {}  # (view, sequence) -> message
        self.prepare_log: Dict[Tuple[int, int], Dict[str, Dict]] = {}  # (view, sequence) -> {node_id -> message}
        self.commit_log: Dict[Tuple[int, int], Dict[str, Dict]] = {}  # (view, sequence) -> {node_id -> message}
        
        # Node health tracking
        self.node_health = {node_id: {'last_seen': 0, 'failures': 0} for node_id in self.nodes}
        self.health_check_interval = 5  # seconds between health checks
        self.last_health_check = 0
        self.max_failures = 3  # Number of failures before marking node as inactive
        self.view_change_timeout = 10  # seconds to wait before initiating view change
        self.last_primary_activity = time.time()

        print(f"[Validator] Initialized node {self.node_id} at {self.node_url}")
        print(f"[Validator] Known nodes: {list(self.nodes.keys())}")
        
        # Start health check thread
        self.health_check_thread = threading.Thread(target=self._health_check_loop, daemon=True)
        self.health_check_thread.start()
    
    def _health_check_loop(self):
        """Background thread to periodically check node health and recover inactive nodes."""
        while True:
            time.sleep(10)  # Check every 10 seconds
            self._check_inactive_nodes()

    def _check_inactive_nodes(self):
        """Check inactive nodes to see if they've recovered."""
        current_time = time.time()
        for node_id, node_info in list(self.nodes.items()):
            # Skip active nodes or nodes that were recently checked
            if node_info.get('active', True) or \
               current_time - node_info.get('last_attempt', 0) < 10:
                continue
                
            print(f"[Health Check] Attempting to recover node {node_id}")
            try:
                # Try to reach the node's health endpoint
                health_url = f"{node_info['url'].rstrip('/')}/health/"
                response = requests.get(health_url, timeout=5)
                if response.status_code == 200:
                    # Node is back online
                    node_info['active'] = True
                    node_info['failures'] = 0
                    node_info['last_seen'] = time.time()
                    print(f"[Health Check] Node {node_id} is back online")
            except Exception as e:
                # Node is still down, will retry later
                print(f"[Health Check] Node {node_id} still unreachable: {str(e)}")
            finally:
                node_info['last_attempt'] = current_time

    def broadcast(self, endpoint: str, message: dict, exclude: List[str] = None) -> List[Tuple[str, bool, dict]]:
        """
        Broadcast a message to all nodes in the network with enhanced error handling and logging.
        
        Args:
            endpoint: API endpoint to call on each node (e.g., 'api/consensus/prepare/')
            message: Message to send (will be converted to JSON)
            exclude: List of node IDs to exclude from broadcast
            
        Returns:
            List of tuples containing (node_id, success, response_data) for each node
        """
        exclude = exclude or []
        results = []
        
        def send_to_node(node_id: str, node_info: dict) -> tuple:
            """Helper function to send a message to a single node."""
            if node_id in exclude:
                print(f"[Broadcast] Skipping {node_id} (excluded)")
                return (node_id, False, {'error': 'Node excluded'})
                
            if not node_info.get('active', True):
                print(f"[Broadcast] Skipping {node_id} (inactive)")
                return (node_id, False, {'error': 'Node inactive'})
            
            # Ensure URL is properly formatted
            base_url = node_info['url'].rstrip('/')
            endpoint_clean = endpoint.lstrip('/')
            url = f"{base_url}/{endpoint_clean}"
            
            print(f"[Broadcast] Sending to {node_id} at {url}")
            
            try:
                # Prepare headers
                headers = {
                    'Content-Type': 'application/json',
                    'X-Node-ID': self.node_id,
                    'X-Message-Type': endpoint_clean.split('/')[-1]  # e.g., 'pre-prepare', 'prepare', 'commit'
                }
                
                # Log the message being sent (truncate if too long)
                msg_str = json.dumps(message, indent=2)
                print(f"[Broadcast] Sending to {node_id} at {url}: {msg_str[:200]}...")
                
                # Send the request with increased timeout
                response = requests.post(
                    url,
                    json=message,
                    headers=headers,
                    timeout=10  # Increased timeout to 10 seconds
                )
                
                # Update node status
                node_info['last_seen'] = time.time()
                node_info['active'] = True
                
                # Try to parse JSON response
                try:
                    response_data = response.json()
                    print(f"[Broadcast] Response from {node_id} ({response.status_code}): {json.dumps(response_data, indent=2)[:200]}...")
                    return (node_id, True, response_data)
                except ValueError as e:
                    error_msg = f"Invalid JSON response from {node_id}: {response.text[:200]}"
                    print(f"[Broadcast] {error_msg}")
                    return (node_id, False, {'error': error_msg})
                
            except requests.exceptions.Timeout:
                error_msg = f"Request to {node_id} timed out after 10 seconds"
                print(f"[Broadcast] {error_msg}")
                node_info['failures'] = node_info.get('failures', 0) + 1
                if node_info['failures'] >= self.max_failures:
                    node_info['active'] = False
                    print(f"[Broadcast] Marking {node_id} as inactive after {self.max_failures} failures")
                return (node_id, False, {'error': error_msg})
                
            except requests.exceptions.ConnectionError as e:
                error_msg = f"Connection error to {node_id} at {url}: {str(e)}"
                print(f"[Broadcast] {error_msg}")
                node_info['failures'] = node_info.get('failures', 0) + 1
                if node_info['failures'] >= self.max_failures:
                    node_info['active'] = False
                    print(f"[Broadcast] Marking {node_id} as inactive after {self.max_failures} failures")
                return (node_id, False, {'error': error_msg})
                
            except requests.exceptions.RequestException as e:
                error_msg = f"Request error to {node_id} at {url}: {str(e)}"
                print(f"[Broadcast] {error_msg}")
                node_info['failures'] = node_info.get('failures', 0) + 1
                if node_info['failures'] >= self.max_failures:
                    node_info['active'] = False
                    print(f"[Broadcast] Marking {node_id} as inactive after {self.max_failures} failures")
                return (node_id, False, {'error': error_msg})
                
            except Exception as e:
                error_msg = f"Unexpected error sending to {node_id}: {str(e)}"
                print(f"[Broadcast] {error_msg}")
                node_info['active'] = False
                return (node_id, False, {'error': error_msg})
        
        # Process nodes in parallel with a thread pool
        with ThreadPoolExecutor(max_workers=len(self.nodes)) as executor:
            # Create a future for each node
            future_to_node = {
                executor.submit(send_to_node, node_id, node_info): node_id
                for node_id, node_info in self.nodes.items()
            }
            
            # Process results as they complete
            for future in as_completed(future_to_node):
                node_id = future_to_node[future]
                try:
                    result = future.result()
                    if result is not None:
                        results.append(result)
                except Exception as e:
                    print(f"[Broadcast] Error processing response from {node_id}: {str(e)}")
                    results.append((node_id, False, {'error': str(e)}))
        
        # Log summary of broadcast results
        success_count = sum(1 for r in results if r and r[1] is True)
        total_nodes = len(self.nodes)
        print(f"[Broadcast] Completed: {success_count}/{total_nodes} nodes responded successfully")
        
        return results
    
    def check_node_health(self, node_id: str) -> bool:
        """Check if a node is healthy by sending a health check request."""
        if node_id not in self.nodes:
            return False
            
        try:
            response = requests.get(
                f"{self.nodes[node_id]['url']}/api/health/",
                timeout=3
            )
            if response.status_code == 200:
                self.node_health[node_id] = {
                    'last_seen': time.time(),
                    'failures': 0
                }
                self.nodes[node_id]['active'] = True
                return True
        except Exception as e:
            print(f"[Health] Error checking node {node_id}: {str(e)}")
            
        # Update failure count
        if node_id not in self.node_health:
            self.node_health[node_id] = {'failures': 0, 'last_seen': 0}
            
        self.node_health[node_id]['failures'] += 1
        
        # Mark as inactive if too many failures
        if self.node_health[node_id]['failures'] >= self.max_failures:
            self.nodes[node_id]['active'] = False
            print(f"[Health] Node {node_id} marked as inactive")
            
        return False
        
    def check_primary_health(self) -> bool:
        """Check if the current primary node is healthy."""
        primary_id = self.consensus.get_primary(self.consensus.view)
        if primary_id == self.node_id:
            return True
            
        return self.check_node_health(primary_id)
        
    def start_view_change(self):
        """Initiate a view change to elect a new primary."""
        if self.consensus.is_primary():
            return {"status": "error", "message": "Current node is already primary"}
            
        print(f"[ViewChange] Starting view change from view {self.consensus.view}")
        self.consensus.view += 1
        
        # Check if this node should be the new primary
        if self.consensus.is_primary():
            print(f"[ViewChange] This node is now the primary for view {self.consensus.view}")
            # Process any pending transactions
            if self.pending_transactions:
                print(f"[ViewChange] Processing {len(self.pending_transactions)} pending transactions")
                return self._start_pbft_consensus_flow({
                    'index': Block.objects.count() + 1,
                    'previous_hash': Block.objects.order_by('-index').first().hash if Block.objects.exists() else '0' * 64,
                    'data': {'transactions': self.pending_transactions},
                    'timestamp': int(time.time())
                })
        
        return {"status": "success", "message": f"View changed to {self.consensus.view}"}
        
    def handle_transaction(self, transaction_data: dict) -> dict:
        """
        Handle a new transaction from a client.
        
        Args:
            transaction_data: Transaction data from client
            
        Returns:
            Dict containing the result of the transaction
        """
        print(f"[Validator] Received transaction: {transaction_data}")
        
        # Validate transaction data
        if not isinstance(transaction_data, dict) or 'type' not in transaction_data:
            return {"status": "error", "message": "Invalid transaction format"}
        
        # Add timestamp if not present
        if 'timestamp' not in transaction_data:
            transaction_data['timestamp'] = int(time.time())
            
        # Add transaction to pending pool
        self.pending_transactions.append(transaction_data)
        print(f"[Validator] Added to pending transactions. Total pending: {len(self.pending_transactions)}")
        
        # Check if we need to perform health checks
        current_time = time.time()
        if current_time - self.last_health_check > self.health_check_interval:
            self.last_health_check = current_time
            # Check primary health
            if not self.check_primary_health() and not self.consensus.is_primary():
                print("[Health] Primary node is not responding, initiating view change")
                return self.start_view_change()
        
        # If this is the primary node, start the PBFT process
        if self.consensus.is_primary():
            return self._process_as_primary()
        else:
            return self._forward_to_primary(transaction_data)
            
    def _process_as_primary(self):
        """Process transactions as the primary node."""
        print("[Validator] This is the primary node. Starting PBFT consensus...")
        
        if not self.pending_transactions:
            return {"status": "success", "message": "No transactions to process"}
        
        # Create a block proposal
        last_block = Block.objects.order_by('-index').first()
        block_index = last_block.index + 1 if last_block else 1
        
        # Create block data (without saving yet)
        block_data = {
            'index': block_index,
            'previous_hash': last_block.hash if last_block else '0' * 64,
            'data': {'transactions': self.pending_transactions},
            'timestamp': int(time.time())
        }
        
        # Start PBFT consensus
        print(f"[PBFT] Starting consensus for block {block_index}")
        return self._start_pbft_consensus_flow(block_data)
        
    def _forward_to_primary(self, transaction_data):
        """Forward transaction to the primary node with retry logic."""
        max_retries = 3
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            primary_id = self.consensus.get_primary(self.consensus.view)
            if primary_id not in self.nodes or not self.nodes[primary_id].get('active', True):
                print(f"[Validator] Primary node {primary_id} not available, checking if view change is needed")
                if attempt == max_retries - 1:  # Last attempt
                    return self.start_view_change()
                time.sleep(retry_delay)
                continue
                
            primary_url = self.nodes[primary_id]['url']
            try:
                print(f"[Validator] Forwarding transaction to primary node: {primary_url}")
                response = requests.post(
                    f"{primary_url}/api/transaction/",
                    json=transaction_data,
                    headers={'Content-Type': 'application/json'},
                    timeout=5
                )
                print(f"[Validator] Primary node response: {response.status_code}")
                return response.json()
            except Exception as e:
                print(f"[Validator] Error forwarding to primary: {str(e)}")
                if attempt == max_retries - 1:  # Last attempt
                    print("[Validator] Max retries reached, initiating view change")
                    return self.start_view_change()
                time.sleep(retry_delay)
        
        return {"status": "error", "message": "Failed to forward to primary after multiple attempts"}
    
    def _start_pbft_consensus_flow(self, block_data: dict) -> dict:
        """
        Starts and manages the full PBFT consensus flow from the primary's perspective.
        This method replaces the internal, somewhat simplified, logic previously in handle_transaction.
        """
        # Get current view and generate digest for the block data
        view = self.consensus.view
        sequence = self.consensus.sequence_number  # Will be updated by pre_prepare call
        digest = self.consensus._hash_message(block_data)

        # PHASE 1: Pre-Prepare
        pre_prepare_msg = self.consensus.pre_prepare(block_data)  # This updates internal state
        if 'error' in pre_prepare_msg:
            return {"status": "error", "message": f"Pre-prepare failed: {pre_prepare_msg['error']}"}

        # The primary node's own pre-prepare message is considered valid and implicitly "prepared" by itself.
        self.pre_prepare_log[(view, sequence)] = pre_prepare_msg
        
        # Primary also implicitly sends itself a prepare message and records it
        self.prepare_log.setdefault((view, sequence), {})[self.node_id] = {
            'view': view, 'sequence': sequence, 'digest': digest, 'node_id': self.node_id
        }
        
        # Broadcast pre-prepare message
        print(f"[PBFT Primary] Broadcasting pre-prepare for (view={view}, sequence={sequence})")
        responses_from_replicas = self.broadcast(
            '/api/consensus/pre-prepare/',
            pre_prepare_msg,
            exclude=[self.node_id]
        )

        print("[PBFT Primary] Awaiting prepare messages (simulated wait)...")
        # Simulate waiting for prepare messages (in a real system, this would be event-driven)
        time.sleep(1) 

        # PHASE 1: Pre-Prepare (Primary's action)
        _pre_prepare_msg = self.consensus.pre_prepare(block_data) # Updates consensus state
        if 'error' in _pre_prepare_msg:
            return {"status": "error", "message": f"Pre-prepare failed: {_pre_prepare_msg['error']}"}

        # Primary processes its own pre-prepare as if it received it.
        # This will store it in the pre_prepare_log and prepare_log.
        self.handle_pre_prepare(_pre_prepare_msg) 
        
        # Get the block index from block_data
        block_index = block_data.get('index')
        if block_index is None:
            return {"status": "error", "message": "Block data is missing 'index' field"}
            
        # Broadcast pre-prepare message to all replicas (excluding self)
        print(f"[PBFT Primary] Broadcasting pre-prepare for sequence {block_index}")
        # The actual `pre_prepare_responses` here are just acknowledgements of receipt,
        # not the prepare messages themselves. Replicas will then send prepare messages
        # back to the primary's /api/consensus/prepare endpoint.
        self.broadcast(
            '/api/consensus/pre-prepare/',
            _pre_prepare_msg,
            exclude=[self.node_id] 
        )
        
        # In a real system, the primary would now wait for 2f+1 prepare messages
        # to arrive at its `handle_prepare` endpoint. This is a blocking simulation.
        # This wait should ideally be non-blocking with callbacks or an event loop.
        
        # For simplification, we'll assume a delay and then check the accumulated messages.
        print("[PBFT Primary] Simulating wait for prepare messages...")
        time.sleep(settings.PBFT_PREPARE_TIMEOUT if hasattr(settings, 'PBFT_PREPARE_TIMEOUT') else 2) # Wait for replicas to respond

        view = _pre_prepare_msg['view']
        sequence = _pre_prepare_msg['sequence']
        
        # Check if enough prepare messages have been collected
        # The messages are collected by the `handle_prepare` method when other nodes call it.
        current_prepares = self.prepare_log.get((view, sequence), {})
        
        # Add primary's implicit prepare message to the count
        num_prepares = len(current_prepares)
        
        required_prepares = 2 * self.consensus.faulty_nodes + 1 # includes primary's own
        
        print(f"[PBFT Primary] Collected {num_prepares} prepare messages for (view={view}, sequence={sequence}). Required: {required_prepares}")
        
        if num_prepares >= required_prepares:
            print("[PBFT Primary] Enough prepare messages received. Broadcasting commit.")
            commit_msg = self.consensus.commit(list(current_prepares.values())) # Pass list of prepare messages
            if 'error' in commit_msg:
                return {"status": "error", "message": f"Commit message creation failed: {commit_msg['error']}"}
            
            # Primary processes its own commit message
            self.handle_commit(commit_msg)

            # Broadcast commit message to replicas
            self.broadcast(
                '/api/consensus/commit/',
                commit_msg,
                exclude=[self.node_id]
            )

            # In a real system, primary waits for 2f+1 commit messages
            print("[PBFT Primary] Simulating wait for commit messages...")
            time.sleep(settings.PBFT_COMMIT_TIMEOUT if hasattr(settings, 'PBFT_COMMIT_TIMEOUT') else 2)

            current_commits = self.commit_log.get((view, sequence), {})
            num_commits = len(current_commits)
            required_commits = 2 * self.consensus.faulty_nodes + 1

            print(f"[PBFT Primary] Collected {num_commits} commit messages for (view={view}, sequence={sequence}). Required: {required_commits}")

            if num_commits >= required_commits:
                print(f"[PBFT Primary] Enough commit messages received. Executing block {block_index}.")
                try:
                    blockchain_state, _ = BlockchainState.objects.get_or_create(
                        id=1,
                        defaults={
                            'last_block_number': 0,
                            'total_transactions': 0,
                            'active_nodes': len(self.nodes) + 1
                        }
                    )
                    
                    block = Block.objects.create(
                        index=block_index,
                        previous_hash=block_data['previous_hash'],
                        data=block_data['data'],
                        timestamp=block_data['timestamp'],
                        nonce=0,
                        hash='0' * 64
                    )
                    block.mine_block(difficulty=4)
                    block.save()
                    
                    blockchain_state.update_state(
                        block=block,
                        transaction_count=len(self.pending_transactions)
                    )
                    
                    print(f"[PBFT Primary] Committed block {block.index} with {len(self.pending_transactions)} transactions")
                    self.pending_transactions = [] # Clear pending after successful commit
                    self.consensus.set_state(PBFTState.REPLY) # Move to reply state
                    
                    return {
                        "status": "success", 
                        "message": "Block created and committed via PBFT consensus",
                        "block_index": block.index,
                        "transactions_processed": len(block_data['data']['transactions']) # Corrected count
                    }
                except Exception as e:
                    print(f"[PBFT Primary] Error creating block: {str(e)}")
                    return {"status": "error", "message": f"Block creation failed: {str(e)}"}
            else:
                self.consensus.set_state(PBFTState.REPLY) # Revert to reply state on failure
                return {"status": "error", "message": f"Failed to reach commit consensus. Only {num_commits} commits received."}
        else:
            self.consensus.set_state(PBFTState.REPLY) # Revert to reply state on failure
            return {"status": "error", "message": f"Failed to reach prepare consensus. Only {num_prepares} prepares received."}


    # --- Consensus Message Handlers (for replicas, and primary processing its own/others' messages) ---

    def handle_pre_prepare(self, message: dict) -> dict:
        """
        Handle a pre-prepare message from the primary.
        
        Args:
            message: Pre-prepare message containing view, sequence, digest, and request
            
        Returns:
            Dict containing the result of processing the message with all required fields
        """
        try:
            # Replicas should process pre-prepare messages. Primary broadcasts them.
            # If this method is called on the primary, it means the primary is
            # processing its *own* message as if it were a replica to maintain consistency.
            
            view = message.get('view')
            sequence = message.get('sequence')
            digest = message.get('digest')
            
            # Basic validation of message content
            if not all(k in message for k in ['view', 'sequence', 'digest', 'request']):
                return {
                    'status': 'error', 
                    'message': 'Invalid pre-prepare message format (missing fields)',
                    'node_id': self.node_id,
                    'view': view, 'sequence': sequence, 'digest': digest, 'timestamp': time.time()
                }

            # Verify the pre-prepare message (e.g., digest, view number, sequence number)
            # The _verify_pre_prepare method is assumed to be in PBFTConsensus
            if not self.consensus._verify_pre_prepare(message):
                return {
                    'status': 'error', 
                    'message': 'Pre-prepare message verification failed',
                    'node_id': self.node_id,
                    'view': view, 'sequence': sequence, 'digest': digest, 'timestamp': time.time()
                }
            
            # Store the pre-prepare message
            self.pre_prepare_log[(view, sequence)] = message
            print(f"[Validator {self.node_id}] Received and verified pre-prepare for (view={view}, sequence={sequence})")

            # Create a prepare message
            # The `prepare` method in PBFTConsensus should internally check and update its state
            prepare_msg = self.consensus.prepare(message)
            if 'error' in prepare_msg:
                prepare_msg.update({
                    'node_id': self.node_id,
                    'view': view, 'sequence': sequence, 'digest': digest, 'timestamp': time.time()
                })
                return prepare_msg
            
            # Add self's prepare message to log (important for reaching 2f+1 later)
            self.prepare_log.setdefault((view, sequence), {})[self.node_id] = prepare_msg
            print(f"[Validator {self.node_id}] Created and stored own prepare message for (view={view}, sequence={sequence})")

            # Broadcast prepare message to all other replicas (including primary if it also listens to prepare)
            # Usually, replicas send prepare to all, including the primary.
            prepare_responses = self.broadcast(
                '/api/consensus/prepare/',
                prepare_msg,
                exclude=[self.node_id] # Don't send to self again
            )
            
            success_count = sum(1 for r in prepare_responses if r and r[1])
            print(f"[Validator {self.node_id}] Broadcasted prepare message to {success_count}/{len(self.nodes)} nodes for (view={view}, sequence={sequence})")
            
            return {
                'status': 'success',
                'message': 'Prepare message processed and broadcasted',
                'node_id': self.node_id,
                'view': view,
                'sequence': sequence,
                'digest': digest,
                'timestamp': time.time(),
                'broadcast_results': {
                    'total_nodes': len(self.nodes),
                    'successful': success_count,
                    'failed': len(self.nodes) - success_count
                }
            }
            
        except Exception as e:
            print(f"[Validator {self.node_id}] Error in handle_pre_prepare: {str(e)}")
            return {
                'status': 'error',
                'message': f'Error processing pre-prepare: {str(e)}',
                'node_id': self.node_id,
                'view': message.get('view'),
                'sequence': message.get('sequence'),
                'digest': message.get('digest'),
                'timestamp': time.time()
            }

    def handle_prepare(self, message: dict) -> dict:
        """
        Handle a prepare message from a replica.
        
        Args:
            message: Prepare message containing view, sequence, and digest
            
        Returns:
            Dict containing the result of processing the message with all required fields
        """
        try:
            required_fields = ['view', 'sequence', 'digest', 'node_id']
            if not all(field in message for field in required_fields):
                return {
                    'status': 'error',
                    'message': 'Missing required fields in prepare message',
                    'node_id': self.node_id,
                    'view': message.get('view'),
                    'sequence': message.get('sequence'),
                    'digest': message.get('digest'),
                    'timestamp': time.time()
                }
            
            view = message['view']
            sequence = message['sequence']
            digest = message['digest']
            sender_node_id = message['node_id']

            print(f"[Validator {self.node_id}] Received prepare message from {sender_node_id} for (view={view}, sequence={sequence})")
            
            # Store the prepare message
            self.prepare_log.setdefault((view, sequence), {})[sender_node_id] = message

            # Check if we have 2f + 1 prepare messages for this (view, sequence, digest)
            current_prepares = self.prepare_log.get((view, sequence), {})
            num_prepares = len(current_prepares)
            required_prepares = 2 * self.consensus.faulty_nodes + 1 # including own

            print(f"[Validator {self.node_id}] Collected {num_prepares} prepare messages for (view={view}, sequence={sequence}). Required: {required_prepares}")

            if num_prepares >= required_prepares and self.consensus.current_state != PBFTState.COMMIT:
                # If this node has accumulated enough prepare messages and is not yet in COMMIT state
                print(f"[Validator {self.node_id}] Enough prepare messages received. Moving to COMMIT phase.")
                
                # Create and broadcast commit message
                commit_msg = self.consensus.commit(list(current_prepares.values())) # Pass collected prepare messages
                if 'error' in commit_msg:
                    commit_msg.update({
                        'node_id': self.node_id, 'view': view, 'sequence': sequence, 'digest': digest, 'timestamp': time.time()
                    })
                    return commit_msg
                
                # Store own commit message
                self.commit_log.setdefault((view, sequence), {})[self.node_id] = commit_msg
                self.consensus.set_state(PBFTState.COMMIT) # Update node's state
                
                # Broadcast commit message to all nodes (excluding self)
                commit_responses = self.broadcast(
                    '/api/consensus/commit/',
                    commit_msg,
                    exclude=[self.node_id]
                )
                
                success_count = sum(1 for r in commit_responses if r and r[1])
                print(f"[Validator {self.node_id}] Broadcasted commit message to {success_count}/{len(self.nodes)} nodes for (view={view}, sequence={sequence})")
                
                return {
                    'status': 'success',
                    'message': 'Commit message created and broadcasted',
                    'node_id': self.node_id,
                    'view': view,
                    'sequence': sequence,
                    'digest': digest,
                    'timestamp': time.time(),
                    'broadcast_results': {
                        'total_nodes': len(self.nodes),
                        'successful': success_count,
                        'failed': len(self.nodes) - success_count
                    }
                }
            
            # If not enough prepares, or if already committed, just acknowledge
            return {
                'status': 'success',
                'message': 'Prepare message processed, awaiting more prepares or already committed.',
                'node_id': self.node_id,
                'view': view,
                'sequence': sequence,
                'digest': digest,
                'timestamp': time.time()
            }
            
        except Exception as e:
            print(f"[Validator {self.node_id}] Error in handle_prepare: {str(e)}")
            return {
                'status': 'error',
                'message': f'Error processing prepare: {str(e)}',
                'node_id': self.node_id,
                'view': message.get('view'),
                'sequence': message.get('sequence'),
                'digest': message.get('digest'),
                'timestamp': time.time()
            }

    def handle_commit(self, message: dict) -> dict:
        """
        Handle a commit message from a replica.
        
        Args:
            message: Commit message containing view, sequence, and digest
            
        Returns:
            Dict containing the result of processing the message with all required fields
        """
        try:
            required_fields = ['view', 'sequence', 'digest', 'node_id']
            if not all(field in message for field in required_fields):
                return {
                    'status': 'error',
                    'message': 'Missing required fields in commit message',
                    'node_id': self.node_id,
                    'view': message.get('view'),
                    'sequence': message.get('sequence'),
                    'digest': message.get('digest'),
                    'timestamp': time.time()
                }

            view = message['view']
            sequence = message['sequence']
            digest = message['digest']
            sender_node_id = message['node_id']

            print(f"[Validator {self.node_id}] Received commit message from {sender_node_id} for (view={view}, sequence={sequence})")

            # Store the commit message
            self.commit_log.setdefault((view, sequence), {})[sender_node_id] = message

            # Check if we have 2f + 1 commit messages for this (view, sequence, digest)
            current_commits = self.commit_log.get((view, sequence), {})
            num_commits = len(current_commits)
            required_commits = 2 * self.consensus.faulty_nodes + 1 # including own

            print(f"[Validator {self.node_id}] Collected {num_commits} commit messages for (view={view}, sequence={sequence}). Required: {required_commits}")

            if num_commits >= required_commits and self.consensus.current_state != PBFTState.REPLY:
                # If this node has accumulated enough commit messages and hasn't replied yet
                print(f"[Validator {self.node_id}] Enough commit messages received. Executing request and replying to client.")
                
                # Retrieve the original request data from the pre-prepare log
                pre_prepare_data = self.pre_prepare_log.get((view, sequence))
                if not pre_prepare_data or 'request' not in pre_prepare_data:
                    print(f"[Validator {self.node_id}] Error: Original request data not found for (view={view}, sequence={sequence})")
                    return {
                        'status': 'error',
                        'message': 'Original request data missing for execution',
                        'node_id': self.node_id,
                        'view': view, 'sequence': sequence, 'digest': digest, 'timestamp': time.time()
                    }
                
                block_data = pre_prepare_data['request']
                
                try:
                    # Get or create blockchain state
                    blockchain_state, _ = BlockchainState.objects.get_or_create(
                        id=1,
                        defaults={
                            'last_block_number': 0,
                            'total_transactions': 0,
                            'active_nodes': len(self.nodes) + 1
                        }
                    )
                    
                    block = Block.objects.create(
                        index=block_data['index'],
                        previous_hash=block_data['previous_hash'],
                        data=block_data['data'],
                        timestamp=block_data['timestamp'],
                        nonce=0, # Temporarily
                        hash='0' * 64 # Temporarily
                    )
                    block.mine_block(difficulty=4) # Mine to get actual hash and nonce
                    block.save()
                    
                    blockchain_state.update_state(
                        block=block,
                        transaction_count=len(block_data['data']['transactions'])
                    )
                    
                    print(f"[Validator {self.node_id}] Executed request and created block {block.index}.")
                    self.consensus.set_state(PBFTState.REPLY) # Move to reply state

                    return {
                        'status': 'success',
                        'message': 'Block committed and executed successfully',
                        'node_id': self.node_id,
                        'view': view,
                        'sequence': sequence,
                        'digest': digest,
                        'block_index': block.index,
                        'transactions_processed': len(block_data['data']['transactions']),
                        'timestamp': time.time()
                    }
                except Exception as e:
                    print(f"[Validator {self.node_id}] Error during block creation/execution: {str(e)}")
                    return {
                        'status': 'error',
                        'message': f'Block creation/execution failed: {str(e)}',
                        'node_id': self.node_id,
                        'view': view, 'sequence': sequence, 'digest': digest, 'timestamp': time.time()
                    }
            
            # If not enough commits, or already replied, just acknowledge
            return {
                'status': 'success',
                'message': 'Commit message processed, awaiting more commits or already executed.',
                'node_id': self.node_id,
                'view': view,
                'sequence': sequence,
                'digest': digest,
                'timestamp': time.time()
            }

        except Exception as e:
            print(f"[Validator {self.node_id}] Error in handle_commit: {str(e)}")
            return {
                'status': 'error',
                'message': f'Error processing commit: {str(e)}',
                'node_id': self.node_id,
                'view': message.get('view'),
                'sequence': message.get('sequence'),
                'digest': message.get('digest'),
                'timestamp': time.time()
            }