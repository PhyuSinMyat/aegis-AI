from dataclasses import dataclass


@dataclass
class User:
    user_id: int
    username: str
    email: str
    password_hash: str
    profile_pic: str = ""
    first_name: str = ""
    last_name: str = ""
    date_of_birth: str = ""
    phone_number: str = ""
    created_at: str = ""

    def get_user_id(self) -> int:
        return self.user_id

    def get_username(self) -> str:
        return self.username

    def get_email(self) -> str:
        return self.email

    def get_password_hash(self) -> str:
        return self.password_hash

    def get_profile_pic(self) -> str:
        return self.profile_pic

    def get_first_name(self) -> str:
        return self.first_name

    def get_last_name(self) -> str:
        return self.last_name

    def get_date_of_birth(self) -> str:
        return self.date_of_birth

    def get_phone_number(self) -> str:
        return self.phone_number

    def get_created_at(self) -> str:
        return self.created_at

    def get_full_name(self) -> str:
        """Return full name (first + last) or username if no name set."""
        first = self.first_name.strip()
        last = self.last_name.strip()
        if first and last:
            return f"{first} {last}"
        elif first:
            return first
        elif last:
            return last
        return self.username
