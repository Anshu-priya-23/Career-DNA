from django.urls import path
from . import views
urlpatterns = [
    path('signup/', views.signup_view, name='signup'),
    path('discovery/', views.discovery_view, name='discovery'),
    path('profile/', views.profile_view, name='profile'),
    path('resume/', views.resume_view, name='resume'),
    path('update-task/<int:task_id>/', views.update_task_view, name='update_task'),
]
