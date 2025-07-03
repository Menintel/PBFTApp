from django.urls import path
from . import views
from . import consensus_views
from .consensus_views import HealthCheckView

app_name = 'image_app'

urlpatterns = [
    path('', views.UnsplashGalleryView.as_view(), name='unsplash-gallery'),
    
    # PBFT Consensus API endpoints
    path('api/consensus/pre-prepare/', 
         consensus_views.PrePrepareView.as_view(), 
         name='pre-prepare'),
    path('api/consensus/prepare/', 
         consensus_views.PrepareView.as_view(), 
         name='prepare'),
    path('api/consensus/commit/', 
         consensus_views.CommitView.as_view(), 
         name='commit'),
    
    # Transaction endpoint
    path('api/transaction/', 
         consensus_views.TransactionView.as_view(), 
         name='transaction'),
    
    # Health check endpoint
    path('api/health/', 
         HealthCheckView.as_view(), 
         name='health-check'),
]
