import os
import json
import uuid
import time
import requests
from django.conf import settings
from django.shortcuts import render, redirect
from django.views import View
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from pbft.validator import PBFTValidator
from .models import Transaction, Block, Validator, BlockchainState

class UnsplashGalleryView(View):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.validator = self._get_validator()
    
    def _get_validator(self) -> PBFTValidator:
        """Initialize or get the PBFT validator instance."""
        # In a real implementation, this would be a singleton or managed by Django's app config
        node_id = os.getenv('NODE_ID', 'node_1')
        node_address = os.getenv('NODE_ADDRESS', '127.0.0.1')
        node_port = int(os.getenv('NODE_PORT', 8000))
        
        # Get list of known nodes (in a real app, this would be from a config or database)
        nodes = [
            {'id': 'node_1', 'address': '127.0.0.1', 'port': 8000},
            {'id': 'node_2', 'address': '127.0.0.1', 'port': 8001},
            {'id': 'node_3', 'address': '127.0.0.1', 'port': 8002},
            {'id': 'node_4', 'address': '127.0.0.1', 'port': 8003},
        ]
        
        # Filter out current node
        nodes = [n for n in nodes if n['id'] != node_id]
        
        return PBFTValidator(node_id, node_address, node_port, nodes)
    
    def get(self, request):
        # Get the Unsplash API key from environment variables
        unsplash_key = os.getenv('UNSPLASH_ACCESS_KEY')
        
        if not unsplash_key:
            return render(request, 'image_app/error.html', {
                'error': 'Unsplash API key not configured. Please set UNSPLASH_ACCESS_KEY in your .env file.'
            })
        
        # Get search query from request
        search_query = request.GET.get('q', '').strip()
        
        # Log the search as a transaction if there's a query
        if search_query:
            transaction = {
                'type': 'SEARCH',
                'data': {
                    'query': search_query,
                    'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                    'ip_address': self._get_client_ip(request)
                },
                'timestamp': int(time.time())
            }
            
            # Submit transaction to the PBFT network
            result = self.validator.handle_transaction(transaction)
            
            # In a real implementation, we would wait for consensus
            # For now, we'll just log the result
            print(f"Transaction submitted: {result}")
        
        # Set up headers with the API key
        headers = {
            'Authorization': f'Client-ID {unsplash_key}',
            'Accept-Version': 'v1'
        }
        
        # Parameters for the API request
        params = {
            'per_page': 12,  # Number of photos to fetch
            'orientation': 'landscape'  # Get landscape-oriented photos
        }
        
        # Determine which endpoint to use based on search
        if search_query:
            url = 'https://api.unsplash.com/search/photos'
            params['query'] = search_query
        else:
            url = 'https://api.unsplash.com/photos/random'
            params['count'] = 12  # Only for random endpoint
        
        try:
            # Make the API request
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()  # Raise an exception for HTTP errors
            data = response.json()
            
            # Extract relevant photo data based on whether it's a search or random request
            image_data = []
            photos = data.get('results', []) if search_query else data
            
            for photo in photos:
                # Handle both search and random endpoints response formats
                photo_data = {
                    'id': photo.get('id', ''),
                    'url': photo['urls']['regular'],
                    'thumb': photo['urls']['thumb'],
                    'alt': photo.get('alt_description') or 'Unsplash Image',
                    'photographer': photo['user']['name'],
                    'photographer_url': f"{photo['user']['links']['html']}?utm_source=your_app_name&utm_medium=referral"
                }
                image_data.append(photo_data)
                
                # Log each image view as a transaction
                view_transaction = {
                    'type': 'VIEW',
                    'data': {
                        'image_id': photo.get('id', ''),
                        'image_url': photo['urls']['regular'],
                        'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                        'ip_address': self._get_client_ip(request)
                    },
                    'timestamp': int(time.time())
                }
                self.validator.handle_transaction(view_transaction)
            
            # Get blockchain stats for the template
            blockchain_stats = self._get_blockchain_stats()
            
            # Add search query and blockchain stats to context
            context = {
                'images': image_data,
                'search_query': search_query,
                'has_results': bool(image_data),
                'blockchain': blockchain_stats
            }
            
            return render(request, 'image_app/gallery.html', context)
            
        except requests.RequestException as e:
            return render(request, 'image_app/error.html', {
                'error': f'Error fetching images from Unsplash: {str(e)}'
            })
    
    def _get_client_ip(self, request: HttpRequest) -> str:
        """Get the client's IP address from the request."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0]
        return request.META.get('REMOTE_ADDR', '')
    
    def _get_blockchain_stats(self):
        """Get statistics about the blockchain for display."""
        from .models import Block, Transaction, BlockchainState
        
        try:
            # Get or create blockchain state
            # Use get() first to avoid the tuple unpacking issue with get_or_create
            try:
                blockchain_state = BlockchainState.objects.get(id=1)
            except BlockchainState.DoesNotExist:
                blockchain_state = BlockchainState.objects.create(
                    id=1,
                    last_block_number=0,
                    total_transactions=0,
                    active_nodes=1  # Start with 1 node (self)
                )
            
            # Get the latest block
            last_block = Block.objects.order_by('-index').first()
            
            # Count total transactions if we have blocks
            if last_block:
                total_txs = Transaction.objects.count()
            else:
                total_txs = 0
            
            # Get node information
            node_id = getattr(self.validator, 'node_id', 'node_1')
            is_primary = getattr(self.validator.consensus, 'is_primary', lambda: False)()
            current_view = getattr(self.validator.consensus, 'view', 0)
            
            # Count active nodes (in a real implementation, this would check node status)
            active_nodes = 1  # At least this node is active
            
            return {
                'total_blocks': blockchain_state.last_block_number,
                'total_transactions': blockchain_state.total_transactions,
                'active_nodes': active_nodes,
                'current_view': current_view,
                'node_id': node_id,
                'is_primary': is_primary,
                'last_block_hash': last_block.hash if last_block else 'N/A',
                'last_block_time': last_block.timestamp if last_block else 0
            }
            
        except Exception as e:
            print(f"[ERROR] Error getting blockchain stats: {str(e)}")
            # Fallback to basic info if there's an error
            return {
                'total_blocks': 0,
                'total_transactions': 0,
                'active_nodes': 1,
                'current_view': 0,
                'node_id': getattr(self.validator, 'node_id', 'node_1'),
                'is_primary': getattr(self.validator.consensus, 'is_primary', lambda: False)(),
                'error': str(e)
            }
