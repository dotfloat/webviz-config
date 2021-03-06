import re
import pathlib
import getpass
import datetime
import subprocess  # nosec
import argparse

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa

from .._user_data_dir import user_data_dir
from ..utils import terminal_colors


NAME = x509.Name(
    [
        x509.NameAttribute(NameOID.COUNTRY_NAME, "NO"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Trondheim"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Webviz"),
        x509.NameAttribute(NameOID.COMMON_NAME, f"{getpass.getuser()}"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Webviz"),
    ]
)

CA_KEY_FILENAME = "ca.key"
CA_CRT_FILENAME = "ca.crt"

SERVER_KEY_FILENAME = "server.key"
SERVER_CRT_FILENAME = "server.crt"


def create_key(key_path: pathlib.Path) -> rsa.RSAPrivateKey:

    key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )

    with open(key_path, "wb") as filehandle:
        filehandle.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    return key


def certificate_template(
    subject: x509.name.Name,
    issuer: x509.name.Name,
    public_key: x509.name.Name,
    certauthority: bool = False,
) -> x509.base.CertificateBuilder:

    if certauthority:
        not_valid_after = datetime.datetime.utcnow() + datetime.timedelta(days=365 * 10)

    else:  # shorter valid length for on-the-fly certificates
        not_valid_after = datetime.datetime.utcnow() + datetime.timedelta(days=7)

    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(not_valid_after)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=True
        )
        .add_extension(
            x509.BasicConstraints(ca=certauthority, path_length=None), critical=True
        )
    )


def create_ca(args: argparse.Namespace) -> None:

    directory = user_data_dir()

    directory.mkdir(parents=True, exist_ok=True)

    ca_key_path = directory / CA_KEY_FILENAME
    ca_crt_path = directory / CA_CRT_FILENAME

    if not args.force and ca_crt_path.is_file():
        raise OSError(
            f"The file {ca_crt_path} already exists. Add the "
            "command line flag --force if you want to overwrite"
        )

    key = create_key(ca_key_path)

    subject = issuer = NAME

    cert = certificate_template(
        subject, issuer, key.public_key(), certauthority=True
    ).sign(key, hashes.SHA256(), default_backend())

    with open(ca_crt_path, "wb") as filehandle:
        filehandle.write(cert.public_bytes(serialization.Encoding.PEM))

    # The SHA1 hash here is used only for the user to be able to compare with
    # the SHA1 hash generated by Chrome for visual comparison/comfort by the user.
    sha1 = "-".join(
        re.findall(".{8,8}", cert.fingerprint(hashes.SHA1()).hex())  # nosec
    ).upper()

    installed = False
    if args.auto_install:
        try:
            subprocess.run(  # nosec
                [
                    "certutil",
                    "-d",
                    f"sql:{pathlib.Path.home() / '.pki' / 'nssdb'}",
                    "-A",
                    "-t",
                    "CT,C,c",
                    "-n",
                    "webviz",
                    "-i",
                    ca_crt_path,
                ],
                check=True,
            )
            installed = True
            print(
                f"{terminal_colors.GREEN}{terminal_colors.BOLD}"
                "Successfully installed webviz certificate. "
                "Ready to browse applications on localhost."
                f"{terminal_colors.END}"
            )
        except (PermissionError, subprocess.CalledProcessError):
            print(
                f"{terminal_colors.RED}"
                "Automatic installation of webviz certificate failed. "
                "Falling back to manual installation."
                f"{terminal_colors.END}"
            )

    if not installed:
        print(
            f"""{terminal_colors.BLUE}{terminal_colors.BOLD}
 Created CA key and certificate files (both saved in {directory}).
 Keep the key file ({CA_KEY_FILENAME}) private. The certificate file
 ({CA_CRT_FILENAME}) is not sensitive, and you can import it in
 your browser(s).

 To install it in Chrome:

    - Open Chrome and go to chrome://settings/privacy
    - Select "Manage certificates"
    - Under the tab "Trusted Root Certificatation Authorities", click "Import"
    - Go to {directory} and select the created certificate ({CA_CRT_FILENAME}).
    - Click "Next" and select "Place all certificates in the following store"
    - Click "Next" and then "Finished"
    - If a dialog box appears, you can verify that the displayed thumbprint is
      the same as this one:
       {sha1}
    - Restart Chrome

 When done, you do not have to rerun "webviz certificate" or do this procedure
 before the certificate expiry date has passed. The certificate is only valid
 for localhost.{terminal_colors.END}"""
        )


def create_certificate(directory: pathlib.Path) -> None:
    ca_directory = user_data_dir()
    ca_key_path = ca_directory / CA_KEY_FILENAME
    ca_crt_path = ca_directory / CA_CRT_FILENAME

    server_key_path = directory / SERVER_KEY_FILENAME
    server_crt_path = directory / SERVER_CRT_FILENAME

    if not ca_key_path.is_file() or not ca_crt_path.is_file():
        raise RuntimeError(
            "Could not find CA key and certificate. Please "
            'run the command "webviz certificate --auto-install" and '
            "try again"
        )

    with open(ca_key_path, "rb") as filehandle:
        ca_key = serialization.load_pem_private_key(
            data=filehandle.read(), password=None, backend=default_backend()
        )

    with open(ca_crt_path, "rb") as filehandle:
        ca_crt = x509.load_pem_x509_certificate(
            data=filehandle.read(), backend=default_backend()
        )

    server_key = create_key(server_key_path)

    crt = (
        certificate_template(NAME, ca_crt.subject, server_key.public_key())
        .add_extension(
            critical=True,
            extension=x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=True,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
                key_cert_sign=False,
                crl_sign=False,
            ),
        )
        .add_extension(
            critical=False,
            extension=x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()
            ),
        )
        .sign(private_key=ca_key, algorithm=hashes.SHA256(), backend=default_backend())
    )

    with open(server_crt_path, "wb") as filehandle:
        filehandle.write(crt.public_bytes(encoding=serialization.Encoding.PEM))
