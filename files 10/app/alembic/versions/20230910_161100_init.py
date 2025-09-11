"""init

Revision ID: 20230910_161100
Revises: 
Create Date: 2025-09-10 16:11:00.000000

"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    # Таблицы пользователей, продуктов и остальных моделей
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('phone', sa.String(length=16), nullable=False),
        sa.Column('password_hash', sa.String(length=128), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('role', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('phone')
    )
    # ... остальные create_table для всех моделей (product, wallet, billing и т.д.)

def downgrade():
    op.drop_table('users')
    # ... остальные drop_table