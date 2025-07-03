import hashlib
import json
import time
from enum import Enum
from typing import List, Dict, Any, Optional
from django.conf import settings
from django.core.exceptions import ValidationError

class PBFTState(Enum):    
    PRE_PREPARE = "PRE_PREPARE"
    PREPARE = "PREPARE"
    COMMIT = "COMMIT"
    REPLY = "REPLY"

class PBFTConsensus:
    """
    Practical Byzantine Fault Tolerance (PBFT) consensus implementation.
    This class handles the core PBFT protocol for reaching consensus in the network.
    """
    
    def __init__(self, node_id: str, total_nodes: int):
        """
        Initialize the PBFT consensus instance.
        
        Args:
            node_id: Unique identifier for this node
            total_nodes: Total number of nodes in the network
        """
        self.node_id = node_id
        self.total_nodes = total_nodes
        self.view_number = 1  # Initialize view number to 1 (1-based)
        self.faulty_nodes = (total_nodes - 1) // 3  # Calculate maximum number of faulty nodes
        self.faulty_nodes = (total_nodes - 1) // 3  # Maximum allowed faulty nodes
        self.view = 1  # Current view number (start from 1 for 1-based node numbering)
        self.sequence_number = 0
        self.log = []  # Log of all messages
        self.prepare_messages = {}  # Store prepare messages
        self.commit_messages = {}   # Store commit messages
        self.current_state = PBFTState.REPLY  # Initialize current state to REPLY
        
    def set_state(self, state: PBFTState) -> None:
        """
        Set the current state of the PBFT node.
        
        Args:
            state: The new state to set (from PBFTState enum)
        """
        if not isinstance(state, PBFTState):
            raise ValueError(f"State must be a PBFTState enum value, got {type(state)}")
        self.current_state = state
        print(f"[Consensus] Node {self.node_id} state changed to {state.value}")
        
    def get_primary(self, view: int) -> str:
        """
        Determine the primary node for a given view.
        
        Args:
            view: The view number (1-based)
            
        Returns:
            str: Node ID of the primary for this view (node_1, node_2, etc.)
        """
        # Ensure view is at least 1
        view = max(1, view)
        # Calculate primary node (1-based index)
        node_number = ((view - 1) % self.total_nodes) + 1
        primary_node = f"node_{node_number}"
        print(f"[Consensus] View {view}: Primary node is {primary_node} (total nodes: {self.total_nodes})")
        return primary_node
    
    def is_primary(self) -> bool:
        """Check if the current node is the primary for the current view."""
        return self.get_primary(self.view) == self.node_id
    
    def pre_prepare(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle the pre-prepare phase of PBFT.
        
        Args:
            request: The client request to process
            
        Returns:
            Dict containing the pre-prepare message or error
        """
        if not self.is_primary():
            return {"error": "Not the primary node"}
            
        self.sequence_number += 1
        message = {
            'view': self.view,
            'sequence': self.sequence_number,
            'digest': self._hash_message(request),
            'request': request,
            'node_id': self.node_id,
            'timestamp': time.time()
        }
        
        self.log.append(('pre-prepare', message))
        return message
    
    def prepare(self, pre_prepare_msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle the prepare phase of PBFT.
        
        Args:
            pre_prepare_msg: The pre-prepare message from the primary
            
        Returns:
            Dict containing the prepare message or error
        """
        if self.is_primary():
            return {"error": "Primary node cannot send prepare messages"}
            
        # Verify pre-prepare message
        if not self._verify_pre_prepare(pre_prepare_msg):
            return {"error": "Invalid pre-prepare message"}
            
        prepare_msg = {
            'view': pre_prepare_msg['view'],
            'sequence': pre_prepare_msg['sequence'],
            'digest': pre_prepare_msg['digest'],
            'node_id': self.node_id,
            'timestamp': time.time()
        }
        
        # Store the prepare message
        key = (prepare_msg['view'], prepare_msg['sequence'])
        if key not in self.prepare_messages:
            self.prepare_messages[key] = []
        self.prepare_messages[key].append(prepare_msg)
        
        self.log.append(('prepare', prepare_msg))
        return prepare_msg
    
    def commit(self, prepare_msgs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Handle the commit phase of PBFT.
        
        Args:
            prepare_msgs: List of prepare messages
            
        Returns:
            Dict containing the commit message or None if not enough messages
        """
        if not prepare_msgs:
            return None
            
        # Check if we have enough prepare messages (2f + 1)
        if len(prepare_msgs) < 2 * self.faulty_nodes:
            return None
            
        first_msg = prepare_msgs[0]
        commit_msg = {
            'view': first_msg['view'],
            'sequence': first_msg['sequence'],
            'digest': first_msg['digest'],
            'node_id': self.node_id,
            'timestamp': time.time()
        }
        
        # Store the commit message
        key = (commit_msg['view'], commit_msg['sequence'])
        if key not in self.commit_messages:
            self.commit_messages[key] = []
        self.commit_messages[key].append(commit_msg)
        
        self.log.append(('commit', commit_msg))
        return commit_msg
    
    def reply(self, commit_msgs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Handle the reply phase of PBFT.
        
        Args:
            commit_msgs: List of commit messages
            
        Returns:
            Dict containing the reply message or None if not enough messages
        """
        if not commit_msgs:
            return None
            
        # Check if we have enough commit messages (2f + 1)
        if len(commit_msgs) < 2 * self.faulty_nodes + 1:
            return None
            
        first_msg = commit_msgs[0]
        reply_msg = {
            'view': first_msg['view'],
            'sequence': first_msg['sequence'],
            'digest': first_msg['digest'],
            'node_id': self.node_id,
            'result': 'success',
            'timestamp': time.time()
        }
        
        self.log.append(('reply', reply_msg))
        return reply_msg
    
    def _hash_message(self, message: Any) -> str:
        """Generate a SHA-256 hash of the message.
        
        Args:
            message: The message to hash (can be any JSON-serializable object)
            
        Returns:
            str: Hexadecimal digest of the message
        """
        if isinstance(message, (str, int, float, bool, type(None))):
            message_str = str(message)
        else:
            # Convert dictionaries and other objects to a stable JSON string
            message_str = json.dumps(message, sort_keys=True)
            
        return hashlib.sha256(message_str.encode('utf-8')).hexdigest()
        
    def _verify_pre_prepare(self, message: Dict[str, Any]) -> bool:
        """Verify a pre-prepare message."""
        required_fields = {'view', 'sequence', 'digest', 'request'}
        if not all(field in message for field in required_fields):
            return False
            
        # Verify the primary is correct for this view
        if message.get('node_id') != self.get_primary(message['view']):
            return False
            
        # Verify the message digest
        if message['digest'] != self._hash_message(message['request']):
            return False
            
        return True
    
    @staticmethod
    def _hash_message(message: Dict[str, Any]) -> str:
        """Generate a hash for a message."""
        message_str = json.dumps(message, sort_keys=True).encode()
        return hashlib.sha256(message_str).hexdigest()
    
    def check_view_change(self) -> bool:
        """
        Check if a view change is needed.
        
        Returns:
            bool: True if view change is needed, False otherwise
        """
        # In a real implementation, this would check for timeouts or failures
        # For now, we'll just return False
        return False
    
    def start_view_change(self) -> Dict[str, Any]:
        """
        Start a view change.
        
        Returns:
            Dict containing the view change message
        """
        self.view += 1
        return {
            'type': 'view_change',
            'new_view': self.view,
            'node_id': self.node_id,
            'timestamp': time.time()
        }