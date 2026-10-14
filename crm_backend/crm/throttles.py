from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class PublicIntakeThrottle(AnonRateThrottle):
    scope = "public_intake"


class LoginThrottle(AnonRateThrottle):
    scope = "login"


class CallThrottle(UserRateThrottle):
    scope = "calls"


class PasswordResetRequestThrottle(AnonRateThrottle):
    scope = "password_reset_request"


class PasswordResetConfirmThrottle(AnonRateThrottle):
    scope = "password_reset_confirm"
