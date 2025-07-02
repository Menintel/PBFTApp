from django.urls import path
from . import views
from . import consensus_views

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
]
