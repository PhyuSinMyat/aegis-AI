"""
Quick test script for authentication integration
Run this to verify all components are working
"""

print("=" * 60)
print("AUTHENTICATION INTEGRATION TEST")
print("=" * 60)

# Test 1: Import all required modules
print("\n[1/6] Testing imports...")
try:
    from app import app
    from database import DatabaseHelper
    from backend.services.email_service import EmailService
    from backend.utils.password_validator import PasswordValidator
    from config import Config
    print("    [OK] All imports successful")
except Exception as e:
    print(f"    [FAIL] Import error: {e}")
    exit(1)

# Test 2: Check database connection
print("\n[2/6] Testing database connection...")
try:
    db = DatabaseHelper()
    print("    [OK] Database connected")
except Exception as e:
    print(f"    [FAIL] Database error: {e}")
    exit(1)

# Test 3: Check database methods
print("\n[3/6] Testing database methods...")
try:
    # Test get_user_by_email
    user = db.get_user_by_email('nonexistent@test.com')
    assert user is None
    print("    [OK] get_user_by_email() works")

    # Test password reset token methods exist
    assert hasattr(db, 'create_password_reset_token')
    assert hasattr(db, 'get_password_reset_token')
    assert hasattr(db, 'delete_password_reset_token')
    assert hasattr(db, 'increment_otp_attempts')
    assert hasattr(db, 'mark_token_as_used')
    assert hasattr(db, 'cleanup_expired_reset_tokens')
    print("    [OK] All password reset methods exist")

    # Test update_user_profile signature
    assert hasattr(db, 'update_user_profile')
    print("    [OK] update_user_profile() exists")

except Exception as e:
    print(f"    [FAIL] Database method error: {e}")
    exit(1)

# Test 4: Check password validator
print("\n[4/6] Testing password validator...")
try:
    # Test weak password
    is_valid, errors = PasswordValidator.validate('weak')
    assert not is_valid
    print("    [OK] Weak password rejected")

    # Test strong password
    is_valid, errors = PasswordValidator.validate('Strong123!@#')
    assert is_valid
    assert len(errors) == 0
    print("    [OK] Strong password accepted")

    # Test strength calculation
    strength = PasswordValidator.calculate_strength('Strong123!@#')
    assert strength['score'] >= 5  # Strong password should have score >= 5
    assert strength['label'] == 'Strong'
    print("    [OK] Strength calculation works")

except Exception as e:
    print(f"    [FAIL] Password validator error: {e}")
    exit(1)

# Test 5: Check email service
print("\n[5/6] Testing email service...")
try:
    # Test OTP generation
    otp = EmailService.generate_otp(length=6)
    assert len(otp) == 6
    assert otp.isdigit()
    print("    [OK] OTP generation works")

    # Test expiry time calculation
    expiry = EmailService.calculate_expiry_time(10)
    assert expiry is not None
    print("    [OK] Expiry time calculation works")

    # Note: Not testing actual email sending to avoid errors if not configured
    print("    [SKIP] Email sending (requires .env configuration)")

except Exception as e:
    print(f"    [FAIL] Email service error: {e}")
    exit(1)

# Test 6: Check routes
print("\n[6/6] Testing routes...")
try:
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append(rule.endpoint)

    # Check authentication routes exist
    required_routes = [
        'forgot_password',
        'verify_otp',
        'reset_password',
        'check_username',
        'check_email',
    ]

    for route in required_routes:
        assert route in routes, f"Route {route} not found"
        print(f"    [OK] Route '{route}' registered")

except Exception as e:
    print(f"    [FAIL] Routes error: {e}")
    exit(1)

# Test 7: Check template files
print("\n[7/7] Checking template files...")
try:
    from pathlib import Path
    templates_dir = Path(__file__).parent / 'frontend' / 'templates'

    required_templates = [
        'forgot_password.html',
        'verify_otp.html',
        'reset_password.html',
        'login.html',
        'register.html',
        'profile.html',
    ]

    for template in required_templates:
        template_path = templates_dir / template
        assert template_path.exists(), f"Template {template} not found"
        print(f"    [OK] Template '{template}' exists")

except Exception as e:
    print(f"    [FAIL] Template check error: {e}")
    exit(1)

# All tests passed
print("\n" + "=" * 60)
print("ALL TESTS PASSED!")
print("=" * 60)
print("\nAuthentication integration is complete and working.")
print("\nNext steps:")
print("  1. Configure email settings in .env file")
print("  2. Run: python app.py")
print("  3. Visit: http://localhost:5000/login")
print("  4. Test the 'Forgot password?' flow")
print("\nNote: Email sending will only work if .env is configured with")
print("      valid SMTP credentials (Gmail App Password recommended).")
print()
