from wtforms import BooleanField, Form, PasswordField, StringField, SubmitField
from wtforms import validators


class CreateUserForm(Form):
    username = StringField('Username', [validators.DataRequired(), validators.Length(min=3, max=150)])
    email = StringField('Email', [validators.DataRequired(), validators.Email()])
    password = PasswordField('Password', [validators.DataRequired(), validators.Length(min=8)])
    confirm_password = PasswordField(
        'Confirm Password',
        [validators.DataRequired(), validators.EqualTo('password', message='Passwords do not match')],
    )
    submit = SubmitField('Create Account')


class LoginForm(Form):
    identifier = StringField('Email or Username', [validators.DataRequired(), validators.Length(min=3, max=150)])
    password = PasswordField('Password', [validators.DataRequired()])
    remember_me = BooleanField('Remember me')
    submit = SubmitField('Login')


class ProfileForm(Form):
    first_name = StringField('First Name', [validators.Optional(), validators.Length(max=100)])
    last_name = StringField('Last Name', [validators.Optional(), validators.Length(max=100)])
    date_of_birth = StringField('Date of Birth', [validators.Optional()])
    phone_number = StringField('Phone Number', [validators.Optional(), validators.Length(max=20)])
    submit = SubmitField('Complete Profile')
