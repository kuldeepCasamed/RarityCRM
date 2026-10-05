from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class PublicIntakeThrottle(AnonRateThrottle):
    scope = "public_intake"


class LoginThrottle(AnonRateThrottle):
    scope = "login"


class CallThrottle(UserRateThrottle):
    scope = "calls"
