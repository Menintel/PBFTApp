import os
import subprocess
import time
from pathlib import Path

# Base directory
BASE_DIR = Path(__file__).parent.absolute()

# Node configurations - each node will run on a different port
NODES = [
    {
        'id': 'node_1',
        'port': 8000,
        'db': 'db.sqlite3.node1',
        'log': 'node1.log'
    },
    {
        'id': 'node_2',
        'port': 8001,
        'db': 'db.sqlite3.node2',
        'log': 'node2.log'
    },
    {
        'id': 'node_3',
        'port': 8002,
        'db': 'db.sqlite3.node3',
        'log': 'node3.log'
    },
    {
        'id': 'node_4',
        'port': 8003,
        'db': 'db.sqlite4.node4',
        'log': 'node4.log'
    }
]

def setup_node(node_config):
    """Set up environment for a single node."""
    env = os.environ.copy()
    env['NODE_ID'] = node_config['id']
    env['NODE_ADDRESS'] = '127.0.0.1'
    env['NODE_PORT'] = str(node_config['port'])
    env['DATABASE_URL'] = f'sqlite:///{BASE_DIR}/{node_config["db"]}'
    return env

def run_node(node_config):
    """Run a single node instance."""
    print(f"Starting {node_config['id']} on port {node_config['port']}...")
    
    # Set up environment
    env = setup_node(node_config)
    
    # Create log file if it doesn't exist
    log_path = BASE_DIR / node_config['log']
    log_path.touch(exist_ok=True)
    
    # Run the Django development server
    cmd = [
        'python', 'manage.py', 'runserver',
        f'127.0.0.1:{node_config["port"]}',
        '--noreload',
        '--nothreading'
    ]
    
    with open(log_path, 'w') as log_file:
        process = subprocess.Popen(
            cmd,
            env=env,
            cwd=str(BASE_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT
        )
    
    return process

def initialize_databases():
    """Initialize databases for all nodes."""
    print("Initializing databases...")
    for node in NODES:
        env = setup_node(node)
        print(f"  - Creating database for {node['id']}...")
        
        # Remove existing database if it exists
        db_path = BASE_DIR / node['db']
        if db_path.exists():
            db_path.unlink()
        
        # Run migrations
        subprocess.run(
            ['python', 'manage.py', 'migrate', '--no-input'],
            env=env,
            cwd=str(BASE_DIR),
            capture_output=True
        )
        
        # Create superuser (optional)
        # subprocess.run(
        #     ['python', 'manage.py', 'createsuperuser', '--noinput'],
        #     env=env,
        #     cwd=str(BASE_DIR),
        #     input=b'admin\nadmin@example.com\nadmin\nadmin\n',
        #     stdout=subprocess.DEVNULL
        # )

def main():
    """Main function to run multiple node instances."""
    # Initialize databases first
    initialize_databases()
    
    # Start each node in a separate process
    processes = []
    try:
        for node in NODES:
            process = run_node(node)
            processes.append(process)
            # Give the server a moment to start
            time.sleep(2)
        
        print("\nAll nodes started successfully!")
        print("Press Ctrl+C to stop all nodes")
        
        # Keep the script running
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nStopping all nodes...")
        for process in processes:
            process.terminate()
        print("All nodes stopped.")
    except Exception as e:
        print(f"Error: {e}")
        for process in processes:
            process.terminate()

if __name__ == "__main__":
    main()
