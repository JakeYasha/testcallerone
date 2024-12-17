from django.urls import path
from . import views

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('add/', views.AddPhoneNumbersView.as_view(), name='add_numbers'),
    path('phone/<int:pk>/', views.PhoneNumberDetailView.as_view(), name='phone_detail'),
    path('phone/<int:pk>/delete/', views.delete_phone, name='delete_phone'),
    path('phone/<int:pk>/add_dtmf/', views.add_manual_dtmf, name='add_manual_dtmf'),
    path('phone/<int:pk>/recall/', views.recall_phone, name='recall_phone'),
    path('api/queue-count/', views.queue_count, name='queue_count'),
    path('api/phone-list/', views.phone_list, name='phone_list'),
    path('recordings/<path:filepath>', views.serve_recording, name='serve_recording'),
]
