import argparse
import asyncio
import getpass
import uuid

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.security import password_hash


async def create(email: str, role: str, password: str) -> None:
    async with SessionLocal() as session:
        if await session.scalar(select(User).where(User.email == email)):
            raise SystemExit("User already exists")
        session.add(
            User(
                id=str(uuid.uuid4()),
                email=email,
                password_hash=password_hash(password),
                role=role,
                enabled=True,
            )
        )
        await session.commit()
    print(f"Created {role}: {email}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Human API responder or administrator")
    parser.add_argument("email")
    parser.add_argument("--role", choices=["responder", "admin"], default="responder")
    args = parser.parse_args()
    password = getpass.getpass("Password (minimum 10 characters): ")
    if len(password) < 10:
        raise SystemExit("Password is too short")
    asyncio.run(create(args.email.strip().lower(), args.role, password))


if __name__ == "__main__":
    main()
