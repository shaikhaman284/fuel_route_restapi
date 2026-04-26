"""
route/urls.py

URL patterns for the route application.
Final endpoints:
  POST /api/route/
  GET  /api/route/map/<route_id>/
  GET  /api/health/
"""
from django.urls import path
from .views import RouteView, MapView, HealthView

urlpatterns = [
    path('route/', RouteView.as_view(), name='route'),
    path('route/map/<str:route_id>/', MapView.as_view(), name='map'),
    path('health/', HealthView.as_view(), name='health'),
]
