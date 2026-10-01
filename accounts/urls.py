from django.contrib import admin
from django.urls import path, include
from accounts import views

urlpatterns = [
    path('admin/', admin.site.urls),

    # Authentication
    path('accounts/signup/', views.signup_view, name='signup'),
    path('accounts/', include('django.contrib.auth.urls')),

    # Main Pages
    path('', views.discovery_view, name='discovery'),
    path('accounts/profile/', views.profile_view, name='profile'),

    # Roadmap Task Update (AJAX)
    path(
        'update-task/<int:task_id>/',
        views.update_task_view,
        name='update_task'
    ),
]