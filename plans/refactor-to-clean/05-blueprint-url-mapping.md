# Mapping URL Blueprint

Toàn bộ 61 route. Đường dẫn HTTP không đổi. Chỉ tên endpoint trong `url_for()` thay đổi.

---

## Bảng Route Đầy Đủ

| Endpoint cũ | Endpoint mới | Blueprint | HTTP methods | Pattern URL |
|---|---|---|---|---|
| `register` | `auth.register` | auth | GET POST | `/register` |
| `login` | `auth.login` | auth | GET POST | `/login` |
| `logout` | `auth.logout` | auth | GET | `/logout` |
| `profile` | `auth.profile` | auth | GET POST | `/profile` |
| `dashboard` | `main.dashboard` | main | GET | `/` |
| `global_search` | `main.global_search` | main | GET | `/search` |
| `sync_search_index` | `main.sync_search_index` | main | POST | `/search/sync` |
| `add_connection` | `connections.add_connection` | connections | POST | `/connection/add` |
| `view_connection` | `connections.view_connection` | connections | GET | `/connection/<connection_id>` |
| `edit_connection` | `connections.edit_connection` | connections | POST | `/connection/<connection_id>/edit` |
| `delete_connection` | `connections.delete_connection` | connections | POST | `/connection/<connection_id>/delete` |
| `create_bucket` | `buckets.create_bucket` | buckets | POST | `/connection/<connection_id>/bucket/create` |
| `delete_bucket` | `buckets.delete_bucket` | buckets | POST | `/connection/<connection_id>/bucket/<bucket_name>/delete` |
| `browse_bucket` | `buckets.browse_bucket` | buckets | GET | `/connection/<connection_id>/bucket/<bucket_name>/browse` |
| `multipart_initiate` | `files.multipart_initiate` | files | POST | `/connection/<id>/bucket/<name>/multipart/initiate` |
| `multipart_presign_part` | `files.multipart_presign_part` | files | POST | `/connection/<id>/bucket/<name>/multipart/presign-part` |
| `multipart_complete` | `files.multipart_complete` | files | POST | `/connection/<id>/bucket/<name>/multipart/complete` |
| `multipart_abort` | `files.multipart_abort` | files | POST | `/connection/<id>/bucket/<name>/multipart/abort` |
| `presign_upload` | `files.presign_upload` | files | POST | `/connection/<id>/bucket/<name>/presign-upload` |
| `confirm_upload` | `files.confirm_upload` | files | POST | `/connection/<id>/bucket/<name>/confirm-upload` |
| `create_folder` | `files.create_folder` | files | POST | `/connection/<id>/bucket/<name>/create-folder` |
| `save_text_file` | `files.save_text_file` | files | POST | `/connection/<id>/bucket/<name>/save-text` |
| `rename_object` | `files.rename_object` | files | POST | `/connection/<id>/bucket/<name>/rename` |
| `delete_object` | `files.delete_object` | files | POST | `/connection/<id>/bucket/<name>/delete-object` |
| `delete_objects_bulk` | `files.delete_objects_bulk` | files | POST | `/connection/<id>/bucket/<name>/delete-objects-bulk` |
| `download_zip` | `files.download_zip` | files | POST | `/connection/<id>/bucket/<name>/download-zip` |
| `view_file` | `viewer.view_file` | viewer | GET | `/connection/<id>/bucket/<name>/viewer` |
| `proxy_s3_file` | `viewer.proxy_s3_file` | viewer | GET | `/connection/<id>/bucket/<name>/proxy-file` |
| `office_to_pdf` | `viewer.office_to_pdf` | viewer | GET | `/connection/<id>/bucket/<name>/office-to-pdf` |
| `flv_to_mp4` | `viewer.flv_to_mp4` | viewer | GET | `/connection/<id>/bucket/<name>/flv-to-mp4` |
| `flv_hls_playlist` | `viewer.flv_hls_playlist` | viewer | GET | `/connection/<id>/bucket/<name>/hls/playlist.m3u8` |
| `flv_hls_segment` | `viewer.flv_hls_segment` | viewer | GET | `/connection/<id>/bucket/<name>/hls/segment.ts` |
| `update_video_progress` | `progress.update_video_progress` | progress | POST | `/video/progress` |
| `like_item` | `progress.like_item` | progress | POST | `/api/like` |
| `list_progress` | `progress.list_progress` | progress | GET | `/progress` |
| `delete_progress_item` | `progress.delete_progress_item` | progress | POST | `/progress/delete-item/<int:progress_id>` |
| `delete_progress_bucket` | `progress.delete_progress_bucket` | progress | POST | `/progress/delete-bucket/<bucket_name>` |
| `manage_users` | `admin.manage_users` | admin | GET | `/admin/users` |
| `update_user_quota` | `admin.update_user_quota` | admin | POST | `/admin/user/<int:user_id>/quota` |
| `toggle_user_status` | `admin.toggle_user_status` | admin | POST | `/admin/user/<int:user_id>/toggle-status` |
| `update_user_role` | `admin.update_user_role` | admin | POST | `/admin/user/<int:user_id>/update-role` |
| `admin_functions` | `admin.admin_functions` | admin | GET | `/admin/functions` |
| `bucket_access_list` | `admin.bucket_access_list` | admin | GET | `/admin/bucket-access` |
| `bucket_access_grant` | `admin.bucket_access_grant` | admin | POST | `/admin/bucket-access/grant` |
| `bucket_access_revoke` | `admin.bucket_access_revoke` | admin | POST | `/admin/bucket-access/<int:access_id>/revoke` |
| `view_logs` | `admin.view_logs` | admin | GET | `/logs` |
| `view_system_logs` | `admin.view_system_logs` | admin | GET | `/admin/system-logs` |
| `clear_system_logs` | `admin.clear_system_logs` | admin | POST | `/admin/system-logs/clear` |
| `get_bucket_share_info` | `api.get_bucket_share_info` | api | GET | `/api/bucket-share/info` |
| `search_users` | `api.search_users` | api | GET | `/api/users/search` |
| `add_bucket_share` | `api.add_bucket_share` | api | POST | `/api/bucket-share/add` |
| `update_bucket_share_role` | `api.update_bucket_share_role` | api | POST | `/api/bucket-share/update-role` |
| `update_bucket_general_access` | `api.update_bucket_general_access` | api | POST | `/api/bucket-share/update-general-access` |
| `api_bucket_files` | `api.api_bucket_files` | api | GET | `/api/connection/<id>/bucket/<name>/files` |
| `api_share_file` | `api.api_share_file` | api | POST | `/api/connection/<id>/bucket/<name>/share` |
| `get_video_notes` | `api.get_video_notes` | api | GET | `/api/video/notes` |
| `create_video_note` | `api.create_video_note` | api | POST | `/api/video/notes` |
| `check_paste_conflicts` | `api.check_paste_conflicts` | api | POST | `/api/check-conflicts` |
| `paste_selected_items` | `api.paste_selected_items` | api | POST | `/api/paste` |
| `check_existing_files` | `api.check_existing_files` | api | POST | `/api/connection/<id>/bucket/<name>/check-existing` |
| `resolve_unique_keys` | `api.resolve_unique_keys` | api | POST | `/api/connection/<id>/bucket/<name>/resolve-unique-keys` |

---

## Thay Đổi url_for Theo Từng Template

### `base.html`
```
url_for('login')              → url_for('auth.login')
url_for('logout')             → url_for('auth.logout')
url_for('register')           → url_for('auth.register')
url_for('profile')            → url_for('auth.profile')
url_for('dashboard')          → url_for('main.dashboard')
url_for('global_search')      → url_for('main.global_search')
url_for('list_progress')      → url_for('progress.list_progress')
url_for('manage_users')       → url_for('admin.manage_users')
url_for('bucket_access_list') → url_for('admin.bucket_access_list')
url_for('view_logs')          → url_for('admin.view_logs')
url_for('view_system_logs')   → url_for('admin.view_system_logs')
url_for('admin_functions')    → url_for('admin.admin_functions')
```

### `login.html`
```
url_for('login')    → url_for('auth.login')
url_for('register') → url_for('auth.register')
```

### `register.html`
```
url_for('register') → url_for('auth.register')
url_for('login')    → url_for('auth.login')
```

### `dashboard.html`
```
url_for('add_connection')    → url_for('connections.add_connection')
url_for('view_connection')   → url_for('connections.view_connection', ...)
```

### `buckets.html`
```
url_for('view_connection')   → url_for('connections.view_connection', ...)
url_for('create_bucket')     → url_for('buckets.create_bucket', ...)
url_for('delete_bucket')     → url_for('buckets.delete_bucket', ...)
url_for('browse_bucket')     → url_for('buckets.browse_bucket', ...)
url_for('edit_connection')   → url_for('connections.edit_connection', ...)
url_for('delete_connection') → url_for('connections.delete_connection', ...)
```

### `browser.html`
_(40+ lời gọi url_for, kể cả trong chuỗi JS fetch() — kiểm tra kỹ)_
```
url_for('browse_bucket')            → url_for('buckets.browse_bucket', ...)
url_for('view_file')                → url_for('viewer.view_file', ...)
url_for('proxy_s3_file')            → url_for('viewer.proxy_s3_file', ...)
url_for('create_folder')            → url_for('files.create_folder', ...)
url_for('rename_object')            → url_for('files.rename_object', ...)
url_for('delete_object')            → url_for('files.delete_object', ...)
url_for('delete_objects_bulk')      → url_for('files.delete_objects_bulk', ...)
url_for('download_zip')             → url_for('files.download_zip', ...)
url_for('presign_upload')           → url_for('files.presign_upload', ...)
url_for('confirm_upload')           → url_for('files.confirm_upload', ...)
url_for('multipart_initiate')       → url_for('files.multipart_initiate', ...)
url_for('multipart_presign_part')   → url_for('files.multipart_presign_part', ...)
url_for('multipart_complete')       → url_for('files.multipart_complete', ...)
url_for('multipart_abort')          → url_for('files.multipart_abort', ...)
url_for('save_text_file')           → url_for('files.save_text_file', ...)
url_for('api_share_file')           → url_for('api.api_share_file', ...)
url_for('check_paste_conflicts')    → url_for('api.check_paste_conflicts')
url_for('paste_selected_items')     → url_for('api.paste_selected_items')
url_for('check_existing_files')     → url_for('api.check_existing_files', ...)
url_for('resolve_unique_keys')      → url_for('api.resolve_unique_keys', ...)
url_for('get_bucket_share_info')    → url_for('api.get_bucket_share_info')
url_for('add_bucket_share')         → url_for('api.add_bucket_share')
url_for('update_bucket_share_role') → url_for('api.update_bucket_share_role')
url_for('update_bucket_general_access') → url_for('api.update_bucket_general_access')
url_for('api_bucket_files')         → url_for('api.api_bucket_files', ...)
```

### `viewer.html`
```
url_for('proxy_s3_file')     → url_for('viewer.proxy_s3_file', ...)
url_for('office_to_pdf')     → url_for('viewer.office_to_pdf', ...)
url_for('flv_to_mp4')        → url_for('viewer.flv_to_mp4', ...)
url_for('browse_bucket')     → url_for('buckets.browse_bucket', ...)
url_for('get_video_notes')   → url_for('api.get_video_notes')
url_for('create_video_note') → url_for('api.create_video_note')
url_for('like_item')         → url_for('progress.like_item')
```

### `search.html`
```
url_for('global_search')     → url_for('main.global_search')
url_for('sync_search_index') → url_for('main.sync_search_index')
url_for('view_file')         → url_for('viewer.view_file', ...)
url_for('browse_bucket')     → url_for('buckets.browse_bucket', ...)
```

### `progress.html`
```
url_for('view_file')               → url_for('viewer.view_file', ...)
url_for('browse_bucket')           → url_for('buckets.browse_bucket', ...)
url_for('delete_progress_item')    → url_for('progress.delete_progress_item', ...)
url_for('delete_progress_bucket')  → url_for('progress.delete_progress_bucket', ...)
```

### `users.html`
```
url_for('update_user_quota')  → url_for('admin.update_user_quota', ...)
url_for('toggle_user_status') → url_for('admin.toggle_user_status', ...)
url_for('update_user_role')   → url_for('admin.update_user_role', ...)
```

### `bucket_access.html`
```
url_for('bucket_access_grant')  → url_for('admin.bucket_access_grant')
url_for('bucket_access_revoke') → url_for('admin.bucket_access_revoke', ...)
url_for('search_users')         → url_for('api.search_users')
```

### `system_logs.html`
```
url_for('clear_system_logs') → url_for('admin.clear_system_logs')
```

---

## url_for Nội Bộ Trong Python Views

Route `flv_hls_playlist` gọi `url_for('flv_hls_segment', ...)` bên trong handler của chính nó.
Sau khi chuyển sang blueprint viewer, đổi thành `url_for('viewer.flv_hls_segment', ...)`.

Decorator `login_required` gọi `url_for('login')` → `url_for('auth.login')` (thực hiện ở Phase 3).
Decorator `admin_required` gọi `url_for('login')` → `url_for('auth.login')` và `url_for('dashboard')` → `url_for('main.dashboard')`.
