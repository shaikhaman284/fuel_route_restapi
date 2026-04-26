"""
URL configuration for fuel_route_project.
"""
from django.urls import path, include

urlpatterns = [
    path('api/', include('route.urls')),
]
