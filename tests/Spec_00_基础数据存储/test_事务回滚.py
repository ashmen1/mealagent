from sqlalchemy import text

from conftest import table_count


def test_关联表写入失败时整批回滚且返回500(
    input_factory, db_session, assert_import_error
):
    db_session.execute(
        text(
            "CREATE OR REPLACE FUNCTION spec_00_force_association_failure() "
            "RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'Spec_00 forced write failure'; END; "
            "$$ LANGUAGE plpgsql"
        )
    )
    db_session.execute(
        text(
            "CREATE TRIGGER spec_00_force_association_failure_trigger "
            "BEFORE INSERT ON recipe_ingredients FOR EACH ROW "
            "EXECUTE FUNCTION spec_00_force_association_failure()"
        )
    )
    db_session.commit()
    paths = input_factory.create()

    try:
        assert_import_error(paths, db_session, 500)
        db_session.rollback()
        assert {
            table_name: table_count(db_session, table_name)
            for table_name in [
                "recipes",
                "ingredients",
                "recipe_ingredients",
                "user_profiles",
            ]
        } == {
            "recipes": 0,
            "ingredients": 0,
            "recipe_ingredients": 0,
            "user_profiles": 0,
        }
    finally:
        db_session.rollback()
        db_session.execute(
            text(
                "DROP FUNCTION IF EXISTS spec_00_force_association_failure() CASCADE"
            )
        )
        db_session.commit()

