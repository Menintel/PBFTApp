import json
import requests
import time
from typing import Dict, List, Optional, Tuple
from django.conf import settings
from image_app.models import Block, BlockchainState
from concurrent.futures import ThreadPoolExecutor
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
        self.node_id = node_id or settings.NODE_ID
        self.node_address = node_address or '127.0.0.1'
        self.port = port or int(settings.NODE_PORT)
        self.node_url = f"http://{self.node_address}:{self.port}"
        
        # Initialize node information from settings
        self.nodes = {}
        for node_id, node_url in settings.PBFT_NODES.items():
            if node_id != self.node_id:  # Exclude self
                self.nodes[node_id] = {
                    'url': node_url,
                    'active': True,
                    'last_seen': time.time()
                }
        
        # Initialize consensus
        total_nodes = len(settings.PBFT_NODES)
        self.consensus = PBFTConsensus(self.node_id, total_nodes)
        self.state = PBFTState.REPLY
        self.pending_transactions = []
        self.executor = ThreadPoolExecutor(max_workers=10)
        
        print(f"[Validator] Initialized node {self.node_id} at {self.node_url}")
        print(f"[Validator] Known nodes: {list(self.nodes.keys())}")
    
    def broadcast(self, endpoint: str, message: dict, exclude: List[str] = None) -> List[dict]:
        """
        Broadcast a message to all nodes in the network.
        
        Args:
            endpoint: API endpoint to call on each node
            message: Message to send
            exclude: List of node IDs to exclude from broadcast
            
        Returns:
            List of response dictionaries from each node
        """
        from django.conf import settings
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        exclude = exclude or []
        results = []
        
        def send_to_node(node_id, node_info):
            if node_id in exclude or not node_info.get('active', True):
                return None
                
            url = f"{node_info['url'].rstrip('/')}/{endpoint.lstrip('/')}"
            try:
                response = requests.post(
                    url,
                    json=message,
                    headers={'Content-Type': 'application/json'},
                    timeout=5
                )
                node_info['last_seen'] = time.time()
                node_info['active'] = True
                return (node_id, True, response.json())
            except Exception as e:
                print(f"[Validator] Error broadcasting to {node_id} at {url}: {str(e)}")
                node_info['active'] = False
                return (node_id, False, {'error': str(e)})
        
        # Use thread pool for concurrent requests
        with ThreadPoolExecutor(max_workers=len(self.nodes)) as executor:
            futures = [
                executor.submit(send_to_node, node_id, node_info)
                for node_id, node_info in self.nodes.items()
            ]
        # Wait for all requests to complete
        for future in futures:
            results.append(future.result())
            
        return results
    
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
        
        # If this is the primary node, start the PBFT process
        if self.consensus.is_primary():
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
            
            # PHASE 1: Pre-Prepare
            pre_prepare_msg = self.consensus.pre_prepare(block_data)
            if 'error' in pre_prepare_msg:
                return {"status": "error", "message": f"Pre-prepare failed: {pre_prepare_msg['error']}"}
            
            # Broadcast pre-prepare message to all replicas
            pre_prepare_responses = self.broadcast(
                '/api/consensus/pre-prepare/',
                pre_prepare_msg
            )
            
            # Wait for 2f + 1 prepare messages (including self)
            prepare_messages = []
            for result in pre_prepare_responses:
                if result is not None and len(result) == 3 and result[1] and isinstance(result[2], dict) and 'sequence' in result[2]:
                    prepare_messages.append(result[2])
                elif result is not None:
                    print(f"[Validator] Invalid response format from node: {result}")
            
            # PHASE 2: Prepare
            if len(prepare_messages) >= 2 * self.consensus.faulty_nodes:
                # Collect prepare messages from other nodes
                prepare_msgs = []
                for msg in prepare_messages:
                    prepare_response = self.consensus.prepare(msg)
                    if 'error' not in prepare_response:
                        prepare_msgs.append(prepare_response)
                
                # PHASE 3: Commit
                if len(prepare_msgs) >= 2 * self.consensus.faulty_nodes:
                    commit_msgs = []
                    commit_msg = self.consensus.commit(prepare_msgs)
                    if commit_msg:
                        commit_msgs.append(commit_msg)
                    
                    # Broadcast commit message
                    commit_responses = self.broadcast(
                        '/api/consensus/commit/',
                        commit_msg
                    )
                    
                    # Collect commit messages from other nodes
                    for result in commit_responses:
                        if result is not None and len(result) == 3 and result[1] and isinstance(result[2], dict) and 'sequence' in result[2]:
                            commit_msgs.append(result[2])
                        elif result is not None:
                            print(f"[Validator] Invalid commit response format: {result}")
                    
                    # PHASE 4: Execute (Create block if we have 2f + 1 commits)
                    if len(commit_msgs) >= 2 * self.consensus.faulty_nodes + 1:
                        try:
                            # Get or create blockchain state
                            blockchain_state, _ = BlockchainState.objects.get_or_create(
                                id=1,
                                defaults={
                                    'last_block_number': 0,
                                    'total_transactions': 0,
                                    'active_nodes': len(self.nodes) + 1  # +1 for self
                                }
                            )
                            
                            # Create the block with all required fields
                            block = Block.objects.create(
                                index=block_index,
                                previous_hash=block_data['previous_hash'],
                                data=block_data['data'],
                                timestamp=block_data['timestamp'],
                                nonce=0,  # Will be set during mining
                                hash='0' * 64  # Temporary hash, will be set during mining
                            )
                            
                            # Mine the block to set the correct hash
                            block.mine_block(difficulty=4)
                            block.save()
                            
                            # Update blockchain state
                            blockchain_state.update_state(
                                block=block,
                                transaction_count=len(self.pending_transactions)
                            )
                            
                            print(f"[PBFT] Committed block {block.index} with {len(self.pending_transactions)} transactions")
                            
                            # Clear pending transactions
                            self.pending_transactions = []
                            
                            return {
                                "status": "success", 
                                "message": "Block created and committed via PBFT consensus",
                                "block_index": block.index,
                                "transactions_processed": len(self.pending_transactions)
                            }
                            
                        except Exception as e:
                            print(f"[PBFT] Error creating block: {str(e)}")
                            return {"status": "error", "message": f"Block creation failed: {str(e)}"}
            
            return {"status": "error", "message": "Failed to reach consensus"}
        else:
            # If not primary, forward to primary node
            primary_id = self.consensus.get_primary(self.consensus.view)
            if primary_id in self.nodes and self.nodes[primary_id]['active']:
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
                    return {"status": "error", "message": f"Failed to forward to primary: {str(e)}"}
            else:
                print(f"[Validator] Primary node {primary_id} not available or inactive")
                return {"status": "error", "message": "Primary node not available"}
    
    def _start_pbft_consensus(self, transaction_data: dict) -> dict:
        """
        Start the PBFT consensus process for a transaction.
        
        Args:
            transaction_data: Transaction data to reach consensus on
            
        Returns:
            Dict containing the result of the consensus process
        """
        # Pre-prepare phase
        pre_prepare = self.consensus.pre_prepare(transaction_data)
        if 'error' in pre_prepare:
            return {"status": "error", "message": f"Pre-prepare failed: {pre_prepare['error']}"}
        
        # Broadcast pre-prepare message to all replicas
        responses = self.broadcast(
            "consensus/pre-prepare",
            pre_prepare,
            exclude=[self.node_id]  # Don't send to self
        )
        
        # Check if we have enough responses (2f + 1)
        successful_responses = [r for r in responses if r[0]]
        if len(successful_responses) < 2 * self.consensus.faulty_nodes:
            return {"status": "error", "message": "Not enough replicas responded to pre-prepare"}
        
        # For now, we'll assume the transaction is committed
        # In a full implementation, we would wait for prepare and commit phases
        return {
            "status": "success",
            "message": "Transaction accepted for processing",
            "sequence": pre_prepare['sequence'],
            "view": pre_prepare['view']
        }
    
    def handle_pre_prepare(self, message: dict) -> dict:
        """
        Handle a pre-prepare message from the primary.
        
        Args:
            message: Pre-prepare message
            
        Returns:
            Dict containing the result of processing the message
        """
        if self.consensus.is_primary():
            return {"status": "error", "message": "Primary node cannot process pre-prepare messages"}
        
        # Verify the pre-prepare message
        if not self.consensus._verify_pre_prepare(message):
            return {"status": "error", "message": "Invalid pre-prepare message"}
        
        # Create a prepare message
        prepare_msg = self.consensus.prepare(message)
        if 'error' in prepare_msg:
            return prepare_msg
        
        # Broadcast prepare message to all nodes
        self.broadcast(
            "consensus/prepare",
            prepare_msg,
            exclude=[self.node_id]
        )
        
        return {"status": "success", "message": "Prepare message sent"}
    
    def handle_prepare(self, message: dict) -> dict:
        """
        Handle a prepare message from a replica.
        
        Args:
            message: Prepare message
            
        Returns:
            Dict containing the result of processing the message
        """
        # In a full implementation, we would:
        # 1. Verify the prepare message
        # 2. Collect prepare messages until we have 2f + 1 matching ones
        # 3. Create and broadcast a commit message
        # 4. Wait for 2f + 1 commit messages
        # 5. Execute the request and send a reply to the client
        
        # For now, we'll just log the prepare message
        return {"status": "success", "message": "Prepare message received"}
    
    def check_health(self) -> dict:
        """
        Check the health of the validator and its connections.
        
        Returns:
            Dict containing health information
        """
        status = {
            'node_id': self.node_id,
            'address': self.node_address,
            'port': self.port,
            'is_primary': self.consensus.is_primary(),
            'view': self.consensus.view,
            'state': self.state.value,
            'active_nodes': sum(1 for n in self.nodes.values() if n['active']),
            'total_nodes': len(self.nodes) + 1,  # +1 for self
            'pending_transactions': len(self.pending_transactions)
        }
        return status