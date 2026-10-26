import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env_file() -> None:
    """Load standard KEY=value entries plus pasted Bedrock API key labels."""
    env_path = BASE_DIR / '.env'
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        if ':' in line:
            name, value = line.split(':', 1)
            if name.strip().lower() in {'api key', 'bedrock api key', 'aws bedrock api key'}:
                os.environ.setdefault('AWS_BEARER_TOKEN_BEDROCK', value.strip().strip('"').strip("'"))
            continue

        if '=' in line:
            name, value = line.split('=', 1)
            clean_name = name.strip()
            clean_value = value.strip().strip('"').strip("'")
            if clean_name in {'BEDROCK_API_KEY', 'AWS_BEDROCK_API_KEY'}:
                os.environ.setdefault('AWS_BEARER_TOKEN_BEDROCK', clean_value)
            else:
                os.environ.setdefault(clean_name, clean_value)


_load_env_file()


def _env(name: str, default: str = '') -> str:
    return os.getenv(name, os.getenv(name.lower(), default))


def _env_bool(name: str, default: str = '0') -> bool:
    return _env(name, default).strip().lower() in {'1', 'true', 'yes', 'on'}


class Config:
    BASE_DIR = BASE_DIR
    BACKEND_DIR = BASE_DIR / 'backend'
    PROMPTS_DIR = BACKEND_DIR / 'prompts'
    EXTRACTION_PROMPT_PATH = Path(
        os.getenv('EXTRACTION_PROMPT_PATH', str(PROMPTS_DIR / 'extraction_prompt.txt'))
    )

    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-change-me')
    FLASK_DEBUG = _env_bool('FLASK_DEBUG', '0')

# Cross-origin cookie settings — required for the browser extension
    # to send session cookies from chrome-extension:// to the Flask server.
    # On Vercel (HTTPS) these MUST be True/None; on localhost they are relaxed.
    _is_production = os.getenv('FLASK_ENV', 'development') == 'production'
    SESSION_COOKIE_SECURE   = _is_production          # True on Vercel (HTTPS only)
    SESSION_COOKIE_SAMESITE = 'None' if _is_production else 'Lax'
    SESSION_COOKIE_HTTPONLY = True
    
    AWS_ACCESS_KEY_ID = _env('AWS_ACCESS_KEY_ID').strip()
    AWS_SECRET_ACCESS_KEY = _env('AWS_SECRET_ACCESS_KEY').strip()
    AWS_REGION = _env('AWS_REGION', 'ap-southeast-1').strip()
    AWS_BEDROCK_MODEL_ID = _env('AWS_BEDROCK_MODEL_ID', 'global.anthropic.claude-sonnet-4-6').strip()
    AWS_BEARER_TOKEN_BEDROCK = _env('AWS_BEARER_TOKEN_BEDROCK').strip()

    BEDROCK_MAX_TOKENS = int(os.getenv('BEDROCK_MAX_TOKENS', '12000'))
    BEDROCK_RETRY_MAX_TOKENS = int(os.getenv('BEDROCK_RETRY_MAX_TOKENS', '18000'))
    BEDROCK_TEXT_TEMPERATURE = float(os.getenv('BEDROCK_TEXT_TEMPERATURE', '0'))
    BEDROCK_CONNECT_TIMEOUT = int(os.getenv('BEDROCK_CONNECT_TIMEOUT', '20'))
    BEDROCK_READ_TIMEOUT = int(os.getenv('BEDROCK_READ_TIMEOUT', '120'))
    BEDROCK_MAX_ATTEMPTS = int(os.getenv('BEDROCK_MAX_ATTEMPTS', '3'))
    BEDROCK_API_RETRIES = int(os.getenv('BEDROCK_API_RETRIES', '2'))

    USE_MOCK_LLM = os.getenv('USE_MOCK_LLM', '0').strip().lower() in {'1', 'true', 'yes'}
    DEBUG_LLM = os.getenv('DEBUG_LLM', '1').strip().lower() in {'1', 'true', 'yes'}

    # Email configuration for password reset
    MAIL_SERVER = _env('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', '587'))
    MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', '1').strip().lower() in {'1', 'true', 'yes'}
    MAIL_USE_SSL = os.getenv('MAIL_USE_SSL', '0').strip().lower() in {'1', 'true', 'yes'}
    MAIL_USERNAME = _env('MAIL_USERNAME', '')
    MAIL_PASSWORD = _env('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = _env('MAIL_DEFAULT_SENDER', _env('MAIL_USERNAME', 'noreply@aegis.com'))

    # OTP configuration
    OTP_EXPIRY_MINUTES = int(os.getenv('OTP_EXPIRY_MINUTES', '10'))
    OTP_MAX_ATTEMPTS = int(os.getenv('OTP_MAX_ATTEMPTS', '5'))

    @classmethod
    def bedrock_session_kwargs(cls) -> dict:
        """Build kwargs for boto3.Session. Validates credentials are present."""
        if cls.AWS_BEARER_TOKEN_BEDROCK:
            return {}

        if not cls.AWS_ACCESS_KEY_ID or not cls.AWS_SECRET_ACCESS_KEY:
            raise ValueError(
                'AWS credentials not configured. Please set AWS_BEARER_TOKEN_BEDROCK '
                'or AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in your .env file.'
            )

        kwargs = {}
        if cls.AWS_ACCESS_KEY_ID:
            kwargs['aws_access_key_id'] = cls.AWS_ACCESS_KEY_ID
        if cls.AWS_SECRET_ACCESS_KEY:
            kwargs['aws_secret_access_key'] = cls.AWS_SECRET_ACCESS_KEY
        if cls.AWS_REGION:
            kwargs['region_name'] = cls.AWS_REGION
        return kwargs

    @classmethod
    def resolve_bedrock_model_id(cls, model_id: str | None = None) -> str:
        final_model_id = (model_id or cls.AWS_BEDROCK_MODEL_ID).strip()
        if not final_model_id:
            raise ValueError('AWS_BEDROCK_MODEL_ID is missing. Add it to .env or pass model_id.')
        return final_model_id
