import os
import shutil
import tempfile
import urllib.parse

from flask import Blueprint, Response, current_app, flash, g, redirect, render_template, request, stream_with_context, url_for

from infrastructure.media.ffmpeg import probe_video_duration, start_flv_to_mp4_transcode, start_hls_segment_transcode
from infrastructure.media.libreoffice import convert_to_pdf
from infrastructure.persistence.models import ItemLike, S3Connection, UserBucket, VideoProgress
from use_cases.access_control import (
    check_bucket_access as access_control_check_bucket_access,
    check_bucket_edit_access as access_control_check_bucket_edit_access,
)
from use_cases.file_type import classify_file_type


bp = Blueprint('viewer', __name__)


def _get_s3_client(connection, endpoint_url=None):
    return current_app.config['GET_S3_CLIENT'](connection, endpoint_url=endpoint_url)


def _fix_s3_url(url):
    return current_app.config['FIX_S3_URL'](url)


@bp.route('/connection/<connection_id>/bucket/<bucket_name>/viewer')
def view_file(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.args.get('key')

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit', 'public_upload']

    if not is_public and g.user is None:
        flash('Please log in to continue.', 'error')
        return redirect(url_for('auth.login'))

    if not access_control_check_bucket_access(g.user, conn, bucket_name):
        flash('Permission Denied.', 'error')
        return redirect(url_for('connections.view_connection', connection_id=connection_id))

    if not key:
        flash('No file key specified for viewing.', 'error')
        return redirect(url_for('buckets.browse_bucket', connection_id=connection_id, bucket_name=bucket_name))

    try:
        public_endpoint = conn.upload_endpoint if (conn.upload_endpoint and conn.upload_endpoint.strip()) else conn.endpoint_url
        s3 = _get_s3_client(conn, endpoint_url=public_endpoint)
        presigned_url = _fix_s3_url(s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': key},
            ExpiresIn=3600
        ))

        filename = key.split('/')[-1]
        ext = filename.split('.')[-1].lower() if '.' in filename else ''
        file_type = classify_file_type(ext)

        is_local_endpoint = False
        if conn.endpoint_url:
            parsed_url = urllib.parse.urlparse(conn.endpoint_url)
            hostname = parsed_url.hostname or ''
            if hostname in ['localhost', '127.0.0.1'] or hostname.startswith('192.168.') or hostname.startswith('10.'):
                is_local_endpoint = True

        resume_seconds = 0
        if g.user:
            progress = VideoProgress.query.filter_by(
                user_id=g.user.id,
                connection_name=conn.name,
                bucket_name=bucket_name,
                file_key=key
            ).first()
            resume_seconds = progress.seconds_watched if (progress and progress.seconds_watched > 0) else 0

        is_https_site = request.is_secure or request.headers.get('X-Forwarded-Proto', '').lower() == 'https'
        is_http_s3 = conn.endpoint_url and conn.endpoint_url.startswith('http://')
        use_proxy = (is_https_site and is_http_s3 and file_type in ['pdf', 'video', 'audio', 'image', 'text']) or file_type == 'text'

        if use_proxy:
            file_url = url_for('viewer.proxy_s3_file', connection_id=connection_id, bucket_name=bucket_name, key=key)
        else:
            file_url = presigned_url

        can_edit = access_control_check_bucket_edit_access(g.user, conn, bucket_name)

        like_record = ItemLike.query.filter_by(connection_name=conn.name, bucket_name=bucket_name, file_key=key).first()
        initial_likes = like_record.like_count if like_record else 0

        return render_template(
            'viewer.html',
            connection=conn,
            bucket_name=bucket_name,
            key=key,
            filename=filename,
            file_type=file_type,
            presigned_url=file_url,
            is_local_endpoint=is_local_endpoint,
            resume_seconds=resume_seconds,
            can_edit=can_edit,
            initial_likes=initial_likes
        )
    except Exception as e:
        flash(f'Could not view file: {str(e)}', 'error')
        return redirect(url_for('buckets.browse_bucket', connection_id=connection_id, bucket_name=bucket_name))


@bp.route('/connection/<connection_id>/bucket/<bucket_name>/proxy-file')
def proxy_s3_file(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.args.get('key')

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit', 'public_upload']

    if not is_public and g.user is None:
        return 'Authentication required', 401

    if not access_control_check_bucket_access(g.user, conn, bucket_name):
        return 'Permission Denied', 403

    if not key:
        return 'Missing file key', 400

    try:
        s3 = _get_s3_client(conn)
        kwargs = {'Bucket': bucket_name, 'Key': key}

        range_header = request.headers.get('Range')
        if range_header:
            kwargs['Range'] = range_header

        s3_object = s3.get_object(**kwargs)
        status_code = 206 if range_header else 200

        headers = {
            'Content-Type': s3_object.get('ContentType', 'application/octet-stream'),
            'Content-Length': str(s3_object.get('ContentLength', '')),
            'Accept-Ranges': 'bytes',
            'Content-Disposition': f'inline; filename="{urllib.parse.quote(key.split("/")[-1])}"'
        }

        if 'ContentRange' in s3_object:
            headers['Content-Range'] = s3_object['ContentRange']

        def generate():
            body = s3_object['Body']
            for chunk in body.iter_chunks(chunk_size=1024 * 64):
                yield chunk

        return Response(stream_with_context(generate()), status=status_code, headers=headers)
    except Exception as e:
        return f'Error proxying file: {str(e)}', 500


@bp.route('/connection/<connection_id>/bucket/<bucket_name>/office-to-pdf')
def office_to_pdf(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.args.get('key')

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit', 'public_upload']

    if not is_public and g.user is None:
        return 'Authentication required', 401

    if not access_control_check_bucket_access(g.user, conn, bucket_name):
        return 'Permission Denied', 403

    if not key:
        return 'Missing file key', 400

    temp_dir = tempfile.mkdtemp()
    try:
        s3 = _get_s3_client(conn)
        filename = key.split('/')[-1]
        input_path = os.path.join(temp_dir, filename)

        s3.download_file(bucket_name, key, input_path)
        pdf_path = convert_to_pdf(input_path, temp_dir)

        with open(pdf_path, 'rb') as f:
            pdf_data = f.read()

        headers = {
            'Content-Type': 'application/pdf',
            'Content-Disposition': f'inline; filename="{urllib.parse.quote(os.path.basename(pdf_path))}"'
        }
        return Response(pdf_data, status=200, headers=headers)
    except Exception as e:
        if 'timed out' in str(e).lower():
            return 'Conversion timed out', 504
        return f'Error converting file: {str(e)}', 500
    finally:
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


@bp.route('/connection/<connection_id>/bucket/<bucket_name>/flv-to-mp4')
def flv_to_mp4(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.args.get('key')

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit', 'public_upload']

    if not is_public and g.user is None:
        return 'Authentication required', 401

    if not access_control_check_bucket_access(g.user, conn, bucket_name):
        return 'Permission Denied', 403

    if not key:
        return 'Missing file key', 400

    try:
        s3 = _get_s3_client(conn)
        temp_dir = tempfile.mkdtemp()
        filename = key.split('/')[-1]
        input_path = os.path.join(temp_dir, filename)

        s3.download_file(bucket_name, key, input_path)
        process = start_flv_to_mp4_transcode(input_path)

        def generate():
            try:
                while True:
                    data = process.stdout.read(4096 * 16)
                    if not data:
                        break
                    yield data
            finally:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    pass
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass

        headers = {
            'Content-Type': 'video/mp4',
            'Content-Disposition': f'inline; filename="{urllib.parse.quote(filename.replace(".flv", ".mp4"))}"'
        }
        return Response(stream_with_context(generate()), status=200, headers=headers)
    except Exception as e:
        return f'Error converting video: {str(e)}', 500


@bp.route('/connection/<connection_id>/bucket/<bucket_name>/hls/playlist.m3u8')
def flv_hls_playlist(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.args.get('key')

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit', 'public_upload']

    if not is_public and g.user is None:
        return 'Authentication required', 401

    if not access_control_check_bucket_access(g.user, conn, bucket_name):
        return 'Permission Denied', 403

    if not key:
        return 'Missing file key', 400

    try:
        s3 = _get_s3_client(conn)
        presigned_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': key},
            ExpiresIn=3600,
        )

        duration = probe_video_duration(presigned_url)
        seg_dur = 6.0
        num_segments = int(duration // seg_dur)
        remainder = duration % seg_dur

        playlist_lines = [
            '#EXTM3U',
            '#EXT-X-VERSION:3',
            f'#EXT-X-TARGETDURATION:{int(seg_dur + 1)}',
            '#EXT-X-MEDIA-SEQUENCE:0',
        ]

        for i in range(num_segments):
            start = i * seg_dur
            seg_url = url_for(
                'viewer.flv_hls_segment',
                connection_id=connection_id,
                bucket_name=bucket_name,
                key=key,
                start=start,
                duration=seg_dur,
            )
            playlist_lines.append(f'#EXTINF:{seg_dur:.2f},')
            playlist_lines.append(seg_url)

        if remainder > 0.1:
            start = num_segments * seg_dur
            seg_url = url_for(
                'viewer.flv_hls_segment',
                connection_id=connection_id,
                bucket_name=bucket_name,
                key=key,
                start=start,
                duration=remainder,
            )
            playlist_lines.append(f'#EXTINF:{remainder:.2f},')
            playlist_lines.append(seg_url)

        playlist_lines.append('#EXT-X-ENDLIST')
        return Response('\n'.join(playlist_lines), mimetype='application/x-mpegURL')
    except Exception as e:
        current_app.logger.error(f'Error generating HLS playlist for {key}: {str(e)}')
        return f'Error generating HLS playlist: {str(e)}', 500


@bp.route('/connection/<connection_id>/bucket/<bucket_name>/hls/segment.ts')
def flv_hls_segment(connection_id, bucket_name):
    conn = S3Connection.query.filter_by(connection_id=connection_id).first_or_404()
    key = request.args.get('key')
    start = request.args.get('start', type=float)
    duration = request.args.get('duration', type=float)

    mapping = UserBucket.query.filter_by(connection_id=conn.id, bucket_name=bucket_name).first()
    is_public = mapping and mapping.access_type in ['public', 'public_view', 'public_edit', 'public_upload']

    if not is_public and g.user is None:
        return 'Authentication required', 401

    if not access_control_check_bucket_access(g.user, conn, bucket_name):
        return 'Permission Denied', 403

    if not key or start is None or duration is None:
        return 'Missing parameters', 400

    try:
        s3 = _get_s3_client(conn)
        presigned_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': key},
            ExpiresIn=3600,
        )

        process = start_hls_segment_transcode(presigned_url, start, duration)

        def generate():
            try:
                while True:
                    data = process.stdout.read(4096 * 16)
                    if not data:
                        break
                    yield data
            finally:
                try:
                    process.terminate()
                    process.wait(timeout=1)
                except Exception:
                    pass

        return Response(stream_with_context(generate()), mimetype='video/MP2T')
    except Exception as e:
        current_app.logger.error(f'Error streaming HLS segment {start} for {key}: {str(e)}')
        return f'Error streaming HLS segment: {str(e)}', 500
