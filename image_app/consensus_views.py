import os
import json
import logging
import time
from django.views import View
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.conf import settings
from django.views.decorators.http import require_http_methods
from django.views.decorators.http import require_GET
from pbft.validator import PBFTValidator

# Set up logging
logger = logging.getLogger(__name__)

@method_decorator(csrf_exempt, name='dispatch')
class HealthCheckView(View):
    """Simple health check endpoint for node monitoring"""
    @method_decorator(require_GET)
    def get(self, request):
        return JsonResponse({
            'status': 'healthy',
            'node_id': getattr(settings, 'NODE_ID', 'unknown'),
            'timestamp': time.time(),
            'version': '1.0'
        })

class ConsensusAPIView(View):
    _validator_instance = None
    
    @classmethod
    def get_validator(cls) -> PBFTValidator:
        """Get or create a singleton validator instance."""
        if cls._validator_instance is None:
            from django.conf import settings
            
            # Get node configuration from settings or environment
            node_id = getattr(settings, 'NODE_ID', os.getenv('NODE_ID', 'node_1'))
            node_address = getattr(settings, 'NODE_ADDRESS', os.getenv('NODE_ADDRESS', '127.0.0.1'))
            node_port = int(getattr(settings, 'NODE_PORT', os.getenv('NODE_PORT', 8000)))
            
            # Get node list from settings or use defaults
            if hasattr(settings, 'PBFT_NODES'):
                nodes = [
                    {'id': nid, 'address': url.split('//')[-1].split(':')[0], 'port': int(url.split(':')[-1].rstrip('/'))}
                    for nid, url in settings.PBFT_NODES.items()
                ]
            else:
                # Default configuration
                nodes = [
                    {'id': 'node_1', 'address': '127.0.0.1', 'port': 8000},
                    {'id': 'node_2', 'address': '127.0.0.1', 'port': 8001},
                    {'id': 'node_3', 'address': '127.0.0.1', 'port': 8002},
                    {'id': 'node_4', 'address': '127.0.0.1', 'port': 8003},
                ]
            
            # Filter out self from node list
            nodes = [n for n in nodes if n['id'] != node_id]
            
            logger.info(f"Initializing PBFTValidator for node {node_id} at {node_address}:{node_port}")
            logger.info(f"Known nodes: {[n['id'] for n in nodes]}")
            
            cls._validator_instance = PBFTValidator(node_id, node_address, node_port, nodes)
        
        return cls._validator_instance
    
    @property
    def validator(self) -> PBFTValidator:
        """Property to access the validator instance."""
        return self.get_validator()

@method_decorator(csrf_exempt, name='dispatch')
class PrePrepareView(ConsensusAPIView):
    def post(self, request):
        try:
            # Log incoming request
            logger.info(f"[PrePrepareView] Received pre-prepare request from {request.META.get('REMOTE_ADDR')}")
            
            # Parse request body
            try:
                data = json.loads(request.body)
                logger.debug(f"[PrePrepareView] Request data: {json.dumps(data, indent=2)[:500]}...")
            except json.JSONDecodeError:
                logger.error("[PrePrepareView] Invalid JSON in request body")
                return JsonResponse({'error': 'Invalid JSON'}, status=400)
            
            # Process the pre-prepare message
            try:
                result = self.validator.handle_pre_prepare(data)
                logger.info(f"[PrePrepareView] Successfully processed pre-prepare: {result}")
                
                # Return a complete response with all required fields
                response_data = {
                    'status': 'success',
                    'sequence': data.get('sequence'),
                    'view': data.get('view'),
                    'digest': data.get('digest'),
                    'node_id': self.validator.node_id,
                    'timestamp': time.time(),
                    'message': 'Pre-prepare message processed',
                    'result': result
                }
                logger.debug(f"[PrePrepareView] Sending response: {response_data}")
                return JsonResponse(response_data)
            except Exception as e:
                logger.exception(f"[PrePrepareView] Error processing pre-prepare: {str(e)}")
                return JsonResponse({'error': f'Error processing pre-prepare: {str(e)}'}, status=500)
                
        except Exception as e:
            logger.exception(f"[PrePrepareView] Unexpected error: {str(e)}")
            return JsonResponse({'error': f'Internal server error: {str(e)}'}, status=500)

@method_decorator(csrf_exempt, name='dispatch')
class PrepareView(ConsensusAPIView):
    def post(self, request):
        try:
            # Log incoming request
            logger.info(f"[PrepareView] Received prepare request from {request.META.get('REMOTE_ADDR')}")
            
            # Parse request body
            try:
                data = json.loads(request.body)
                logger.debug(f"[PrepareView] Request data: {json.dumps(data, indent=2)[:500]}...")
            except json.JSONDecodeError:
                logger.error("[PrepareView] Invalid JSON in request body")
                return JsonResponse({'error': 'Invalid JSON'}, status=400)
            
            # Process the prepare message
            try:
                result = self.validator.handle_prepare(data)
                logger.info(f"[PrepareView] Successfully processed prepare: {result}")
                
                # Return a complete response with all required fields
                response_data = {
                    'status': 'success',
                    'sequence': data.get('sequence'),
                    'view': data.get('view'),
                    'digest': data.get('digest'),
                    'node_id': self.validator.node_id,
                    'timestamp': time.time(),
                    'message': 'Prepare message processed',
                    'result': result
                }
                logger.debug(f"[PrepareView] Sending response: {response_data}")
                return JsonResponse(response_data)
            except Exception as e:
                logger.exception(f"[PrepareView] Error processing prepare: {str(e)}")
                return JsonResponse({'error': f'Error processing prepare: {str(e)}'}, status=500)
                
        except Exception as e:
            logger.exception(f"[PrepareView] Unexpected error: {str(e)}")
            return JsonResponse({'error': f'Internal server error: {str(e)}'}, status=500)

@method_decorator(csrf_exempt, name='dispatch')
class CommitView(ConsensusAPIView):
    def post(self, request):
        try:
            # Log incoming request
            logger.info(f"[CommitView] Received commit request from {request.META.get('REMOTE_ADDR')}")
            
            # Parse request body
            try:
                data = json.loads(request.body)
                logger.debug(f"[CommitView] Request data: {json.dumps(data, indent=2)[:500]}...")
            except json.JSONDecodeError:
                logger.error("[CommitView] Invalid JSON in request body")
                return JsonResponse({'error': 'Invalid JSON'}, status=400)
            
            # Process the commit message
            try:
                result = self.validator.handle_commit(data)
                logger.info(f"[CommitView] Successfully processed commit: {result}")
                
                # Return a complete response with all required fields
                response_data = {
                    'status': 'success',
                    'sequence': data.get('sequence'),
                    'view': data.get('view'),
                    'digest': data.get('digest'),
                    'node_id': self.validator.node_id,
                    'timestamp': time.time(),
                    'message': 'Commit message processed',
                    'result': result
                }
                logger.debug(f"[CommitView] Sending response: {response_data}")
                return JsonResponse(response_data)
            except Exception as e:
                logger.exception(f"[CommitView] Error processing commit: {str(e)}")
                return JsonResponse({'error': f'Error processing commit: {str(e)}'}, status=500)
                
        except Exception as e:
            logger.exception(f"[CommitView] Unexpected error: {str(e)}")
            return JsonResponse({'error': f'Internal server error: {str(e)}'}, status=500)

@method_decorator(csrf_exempt, name='dispatch')
class TransactionView(ConsensusAPIView):
    """
    View to handle incoming transactions from clients or other nodes.
    """
    def post(self, request):
        try:
            # Log incoming request
            logger.info(f"[TransactionView] Received transaction from {request.META.get('REMOTE_ADDR')}")
            
            # Parse request body
            try:
                data = json.loads(request.body)
                logger.debug(f"[TransactionView] Request data: {json.dumps(data, indent=2)[:500]}...")
            except json.JSONDecodeError:
                logger.error("[TransactionView] Invalid JSON in request body")
                return JsonResponse({'error': 'Invalid JSON'}, status=400)
            
            # Process the transaction
            try:
                result = self.validator.handle_transaction(data)
                logger.info(f"[TransactionView] Successfully processed transaction: {result}")
                
                # Return a complete response
                response_data = {
                    'status': 'success',
                    'node_id': self.validator.node_id,
                    'timestamp': time.time(),
                    'message': 'Transaction received and being processed',
                    'result': result
                }
                logger.debug(f"[TransactionView] Sending response: {response_data}")
                return JsonResponse(response_data)
                
            except Exception as e:
                logger.exception(f"[TransactionView] Error processing transaction: {str(e)}")
                return JsonResponse(
                    {'error': f'Error processing transaction: {str(e)}'}, 
                    status=500
                )
                
        except Exception as e:
            logger.exception(f"[TransactionView] Unexpected error: {str(e)}")
            return JsonResponse(
                {'error': f'Internal server error: {str(e)}'}, 
                status=500
            )
