"""Password strength validation utility"""
import re
from typing import List, Tuple


# Common weak passwords that should be rejected
COMMON_WEAK_PASSWORDS = {
    'password', 'password1', 'password123', '12345678', '123456789', '1234567890',
    'qwerty', 'qwerty123', 'abc123', 'letmein', 'welcome', 'welcome1',
    'admin', 'admin123', 'user', 'user123', 'test', 'test123',
    'changeme', 'Password1', 'Password123', 'Qwerty123', 'Abcd1234',
}


class PasswordValidator:
    """Validates password strength with configurable rules"""

    MIN_LENGTH = 8
    MIN_LENGTH_STRONG = 12
    REQUIRE_UPPERCASE = True
    REQUIRE_LOWERCASE = True
    REQUIRE_DIGIT = True
    REQUIRE_SPECIAL = True

    @staticmethod
    def validate(password: str, strict: bool = True) -> Tuple[bool, List[str]]:
        """
        Validate password strength

        Args:
            password: The password to validate
            strict: If True, enforces all security requirements

        Returns:
            Tuple of (is_valid, list_of_error_messages)
        """
        errors = []

        if not password:
            return False, ['Password is required.']

        # Check minimum length
        if len(password) < PasswordValidator.MIN_LENGTH:
            errors.append(f'Password must be at least {PasswordValidator.MIN_LENGTH} characters long.')
            return False, errors

        # Check against common weak passwords
        if password.lower() in COMMON_WEAK_PASSWORDS:
            errors.append('This password is too common and easily guessable. Please choose a stronger password.')

        # Check for username-like patterns (avoid sequential patterns)
        if re.search(r'(.)\1{2,}', password):  # Three or more repeated characters
            errors.append('Password should not contain repeated characters (e.g., "aaa" or "111").')

        if strict:
            # Enforce uppercase requirement
            if PasswordValidator.REQUIRE_UPPERCASE and not re.search(r'[A-Z]', password):
                errors.append('Password must contain at least one uppercase letter (A-Z).')

            # Enforce lowercase requirement
            if PasswordValidator.REQUIRE_LOWERCASE and not re.search(r'[a-z]', password):
                errors.append('Password must contain at least one lowercase letter (a-z).')

            # Enforce digit requirement
            if PasswordValidator.REQUIRE_DIGIT and not re.search(r'[0-9]', password):
                errors.append('Password must contain at least one number (0-9).')

            # Enforce special character requirement
            if PasswordValidator.REQUIRE_SPECIAL and not re.search(r'[^A-Za-z0-9]', password):
                errors.append('Password must contain at least one special character (!@#$%^&*()_+-=[]{}|;:,.<>?).')

        is_valid = len(errors) == 0
        return is_valid, errors

    @staticmethod
    def calculate_strength(password: str) -> dict:
        """
        Calculate password strength score

        Returns:
            Dict with score, label, color, width percentage
        """
        if not password:
            return {'score': 0, 'label': '-', 'color': '#d1d5db', 'width': 0}

        score = 0

        # Length scoring
        if len(password) >= 8:
            score += 1
        if len(password) >= 12:
            score += 1
        if len(password) >= 16:
            score += 1

        # Character variety
        if re.search(r'[a-z]', password):
            score += 1
        if re.search(r'[A-Z]', password):
            score += 1
        if re.search(r'[0-9]', password):
            score += 1
        if re.search(r'[^A-Za-z0-9]', password):
            score += 1

        # Penalize common weak passwords
        if password.lower() in COMMON_WEAK_PASSWORDS:
            score = max(0, score - 3)

        # Penalize repeated characters
        if re.search(r'(.)\1{2,}', password):
            score = max(0, score - 1)

        # Determine label and styling
        if score <= 2:
            return {'score': score, 'label': 'Weak', 'color': '#ef4444', 'width': 33}
        elif score <= 4:
            return {'score': score, 'label': 'Medium', 'color': '#f59e0b', 'width': 66}
        else:
            return {'score': score, 'label': 'Strong', 'color': '#10b981', 'width': 100}

    @staticmethod
    def get_requirements_text() -> str:
        """Get human-readable password requirements"""
        requirements = [
            f'At least {PasswordValidator.MIN_LENGTH} characters',
        ]

        if PasswordValidator.REQUIRE_UPPERCASE:
            requirements.append('one uppercase letter')
        if PasswordValidator.REQUIRE_LOWERCASE:
            requirements.append('one lowercase letter')
        if PasswordValidator.REQUIRE_DIGIT:
            requirements.append('one number')
        if PasswordValidator.REQUIRE_SPECIAL:
            requirements.append('one special character')

        return 'Password must contain: ' + ', '.join(requirements) + '.'
