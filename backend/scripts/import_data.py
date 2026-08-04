# Set-Location D:\codes\A_mealagent_v3
# uv run python -m backend.scripts.import_data

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.storage import (
    BasicDataImportError,
    import_basic_data,
)

from backend.storage.models import Base

root = Path.cwd()

# 创建数据库引擎
engine = create_engine(
    "postgresql+psycopg://mealagent:mealagent@127.0.0.1:5432/mealagent"
)

# 只创建不存在的表，不会删除已有表
Base.metadata.create_all(engine)

try:
    with Session(engine) as session:
        result = import_basic_data(
            root / "datas/processed/Recipes/RecipeComplete.json",
            root / "datas/processed/Ingredients/Ingredients2Nutrition.csv",
            root / "datas/processed/users/50个用户健康档案_归一化.json",
            session,
        )
        print(result)
except BasicDataImportError as exc:
    print(f"导入失败：status_code={exc.status_code}, message={exc}")
finally:
    engine.dispose()