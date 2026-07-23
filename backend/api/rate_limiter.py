from slowapi import Limiter
from slowapi.util import get_remote_address

# Initialize the rate limiter using the client's IP address
limiter = Limiter(key_func=get_remote_address)

def global_key_func():
    """Returns a constant string to be used for global rate limits across all users."""
    return "global"
