# GIRO TLS chain completion

GIRO began serving an InCommon DVG2C certificate on 2026-10-29 but omits the
cross-certificate joining emSign Root TLS CA - G1 to the existing system-trusted
emSign Root CA - G1. Windows can obtain the missing chain automatically; the
Linux collector cannot.

`emsign-tls-g1-cross.crt` is the public cross-certificate from
https://repository.emsign.com/certs/EMIncommonDVG2C.p7c, verified against the
issuer's repository at https://repository.emsign.com/.

Certificate SHA-256:
`9B913476EAB476E21E0887DBCDE44345D1359D28116B83B7CD3E87BD807EBF68`.

The collector loads this certificate only for GIRO, alongside the normal system
roots. Hostname and certificate verification remain enabled. No new self-signed
root or private key is included. Remove this workaround when GIRO serves its
complete chain.
