from infrastructure.persistence.models import AuditLog


def log_action(actor_id, target_user_id, connection_name, bucket_name, action_type, details, *, db_session):
    try:
        log = AuditLog(
            user_id=actor_id,
            target_user_id=target_user_id,
            connection_name=connection_name,
            bucket_name=bucket_name,
            action_type=action_type,
            details=details,
        )
        db_session.add(log)
        db_session.commit()
    except Exception as e:
        print(f"Log Error: {e}")
