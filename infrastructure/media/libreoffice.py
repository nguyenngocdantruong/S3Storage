import os
import subprocess


def convert_to_pdf(input_path, output_dir) -> str:
    user_profile_dir = os.path.join(output_dir, 'user_profile')
    user_installation_url = f"file:///{user_profile_dir.replace(os.sep, '/')}"
    result = subprocess.run(
        [
            'soffice',
            f'-env:UserInstallation={user_installation_url}',
            '--headless',
            '--convert-to',
            'pdf',
            '--outdir',
            output_dir,
            input_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"LibreOffice conversion failed: {result.stderr}")

    base_name = os.path.splitext(os.path.basename(input_path))[0]
    pdf_path = os.path.join(output_dir, f'{base_name}.pdf')
    if not os.path.exists(pdf_path):
        raise FileNotFoundError('Converted PDF not found')
    return pdf_path
