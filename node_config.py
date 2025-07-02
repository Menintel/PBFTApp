"""
Configuration for PBFT nodes in the network.
This file contains the network topology and settings for all nodes.
"""

# Network configuration
NODE_NETWORK = {
    'node_1': {
        'id': 'node_1',
        'address': '127.0.0.1',
        'port': 8000,
        'db': 'db.sqlite3.node1',
        'log': 'logs/node1.log',
        'is_active': True
    },
    'node_2': {
        'id': 'node_2',
        'address': '127.0.0.1',
        'port': 8001,
        'db': 'db.sqlite3.node2',
        'log': 'logs/node2.log',
        'is_active': True
    },
    'node_3': {
        'id': 'node_3',
        'address': '127.0.0.1',
        'port': 8002,
        'db': 'db.sqlite3.node3',
        'log': 'logs/node3.log',
        'is_active': True
    },
    'node_4': {
        'id': 'node_4',
        'address': '127.0.0.1',
        'port': 8003,
        'db': 'db.sqlite3.node4',
        'log': 'logs/node4.log',
        'is_active': True
    }
}

# PBFT consensus settings
PBFT_SETTINGS = {
    'fault_tolerance': 1,  # Maximum number of faulty nodes (f)
    'timeout': 5,  # Seconds to wait for messages
    'batch_size': 10,  # Number of transactions per block
    'checkpoint_interval': 100  # Blocks between checkpoints
}

def get_node_list(exclude_node_id=None):
    """
    Get a list of all nodes, optionally excluding one.
    
    Args:
        exclude_node_id: ID of the node to exclude
        
    Returns:
        List of node configurations
    """
    return [
        node for node_id, node in NODE_NETWORK.items()
        if node_id != exclude_node_id and node['is_active']
    ]

def get_node_url(node_id):
    """Get the base URL for a node."""
    node = NODE_NETWORK.get(node_id)
    if not node:
        return None
    return f"http://{node['address']}:{node['port']}"

def get_node_by_port(port):
    """Get node configuration by port number."""
    for node in NODE_NETWORK.values():
        if node['port'] == port:
            return node
    return None
