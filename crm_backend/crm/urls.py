from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import activity, analytics, calls, public, quotes, roi, team
from .views.leads import LeadViewSet

router = DefaultRouter()
router.register("leads", LeadViewSet, basename="lead")
router.register("users", team.CRMUserViewSet, basename="crmuser")
router.register("marketing-spend", roi.MarketingSpendViewSet, basename="spend")
router.register("treatment-catalog", quotes.CatalogViewSet, basename="catalog")
router.register("branches", team.BranchViewSet, basename="branch")
router.register("doctors", team.DoctorViewSet, basename="doctor")
router.register("notifications/recipients", team.RecipientViewSet, basename="recipient")

urlpatterns = [
    path("auth/login/", team.LoginView.as_view()),
    path("me/", team.MeView.as_view()),
    path("interest/", public.InterestView.as_view()),
    path("me/password/", team.ChangePasswordView.as_view()),
    path("team/invite/", team.InviteView.as_view()),
    path("team/invites/", team.InviteListView.as_view()),
    path("team/invites/<int:pk>/", team.InviteDetailView.as_view()),
    path("notifications/test/", team.NotificationTestView.as_view()),
    path("team/accept-invite/<str:token>/", team.AcceptInviteView.as_view()),
    path("leads/<int:lead_id>/contacts/", activity.ContactList.as_view()),
    path("leads/<int:lead_id>/notes/", activity.NoteList.as_view()),
    path("leads/<int:lead_id>/tasks/", activity.LeadTaskList.as_view()),
    path("notes/<int:pk>/", activity.NoteDetail.as_view()),
    path("tasks/", activity.MyTasks.as_view()),
    path("tasks/<int:pk>/", activity.TaskDetail.as_view()),
    path("appointments/", activity.AppointmentList.as_view()),
    path("appointments/<int:pk>/", activity.AppointmentDetail.as_view()),
    path("leads/<int:lead_id>/quotes/", quotes.LeadQuotes.as_view()),
    path("quotes/", quotes.QuoteList.as_view()),
    path("quotes/<int:pk>/", quotes.QuoteDetail.as_view()),
    path("quotes/<int:pk>/status/", quotes.QuoteStatusView.as_view()),
    path("quotes/<int:pk>/duplicate/", quotes.QuoteDuplicateView.as_view()),
    path("quotes/<int:pk>/pdf/", quotes.QuotePDFView.as_view()),
    path("quotes/<int:pk>/email/", quotes.QuoteEmailView.as_view()),
    path("calls/", calls.CallListView.as_view()),
    path("calls/token/", calls.CallTokenView.as_view()),
    path("calls/initiate/", calls.CallInitiateView.as_view()),
    path("calls/voice-twiml/", calls.VoiceTwimlView.as_view()),
    path("calls/status-callback/", calls.StatusCallbackView.as_view()),
    path("calls/recording-callback/", calls.RecordingCallbackView.as_view()),
    path("calls/<int:pk>/", calls.CallDetailView.as_view()),
    path("calls/<int:pk>/log/", calls.CallLogView.as_view()),
    path("calls/<int:pk>/recording/", calls.CallRecordingView.as_view()),
    path("analytics/dashboard/", analytics.DashboardView.as_view()),
    path("analytics/source-roi/", roi.SourceROIView.as_view()),
    path("marketing-spend/import/", roi.SpendImportView.as_view()),
    path("analytics/rep-performance/", analytics.RepPerformanceView.as_view()),
    path("analytics/response-time/", analytics.ResponseTimeView.as_view()),
    path("", include(router.urls)),
]
