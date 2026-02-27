
import sys
print('Python paths:', sys.path[:3])

# Import and show where waffler_auth is coming from
import waffler_auth
print(f'waffler_auth module location: {waffler_auth.__file__}')

# Check if the function has our debug code
import inspect
source = inspect.getsource(waffler_auth.get_oauth_url)
if 'OAuth: get_oauth_url() called' in source:
    print('✓ Source HAS debug logging')
else:
    print('✗ Source MISSING debug logging')
    print('First 500 chars:', source[:500])
