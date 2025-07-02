from django.urls import path
from . import views

app_name = 'image_app'

urlpatterns = [
    path('', views.UnsplashGalleryView.as_view(), name='unsplash-gallery'),
]
