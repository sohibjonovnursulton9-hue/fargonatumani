import asyncio
import bcrypt
import getpass
from app.config import get_settings
import app.database
from app.models import AdminUser

async def reset_admin():
    settings = get_settings()
    await app.database.init_db(settings.database_url)
    async with app.database.async_session_factory() as session:
        from sqlalchemy import select, update
        from app.models import AdminSession
        username = input('Enter admin username to reset or create (default: super_admin): ').strip() or 'super_admin'

        result = await session.execute(select(AdminUser).where(AdminUser.username == username))
        admin = result.scalar_one_or_none()

        is_new = False
        if not admin:
            print(f'Admin user {username} not found. Creating a new one.')
            is_new = True
            admin = AdminUser(
                username=username,
                full_name='Bosh Administrator',
                role='super_admin',
                is_active=True
            )
            session.add(admin)

        new_password = getpass.getpass('Enter new secure password: ')
        confirm_password = getpass.getpass('Confirm new password: ')

        if new_password != confirm_password:
            print('Passwords do not match.')
            await app.database.close_db()
            return

        if len(new_password) < 12 or len(new_password.encode('utf-8')) > 72:
            print('Password must be at least 12 characters and no more than 72 UTF-8 bytes.')
            await app.database.close_db()
            return

        admin.password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        admin.must_change_password = False

        if not is_new:
            # Invalidate sessions
            await session.execute(update(AdminSession).where(AdminSession.admin_user_id == admin.id).values(is_active=False))
            print('Old sessions invalidated.')

        await session.commit()

        if is_new:
            print(f'\nSuccess! New admin {username} created.')
        else:
            print(f'\nSuccess! Password for {username} has been updated.')
        await app.database.close_db()

if __name__ == '__main__':
    asyncio.run(reset_admin())
