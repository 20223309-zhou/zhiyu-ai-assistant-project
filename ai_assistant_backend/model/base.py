"""所有 ORM 模型共用的声明基类。

整个项目只能有这一个 Base。每个 Base 持有各自独立的 MetaData，
外键只能引用同一个 MetaData 里的表。如果 user_model 和 agent_model
各自定义一个 Base，那么 conversations.user_id 上的 ForeignKey("users.id")
在 agent_model 的 registry 里找不到 users 表，会在映射时抛出:

    sqlalchemy.exc.NoReferencedTableError:
    Foreign key associated with column 'conversations.user_id'
    could not find table 'users'

所以模型类一律继承这里的 Base，不要再在别的模块里定义新的 Base。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
