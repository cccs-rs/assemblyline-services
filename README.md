# Custom services for AssemblyLine 4

A set of custom services extending the capabilities of [AssemblyLine 4](https://github.com/CybercentreCanada/assemblyline).
They are created as a hobby project, so please do not expect production quality. They should work with the latest
version of AssemblyLine 4.

## Services

### ClamAV

This service uses the ClamAV antivirus engine to scan files for viruses. It leverages the daemon mode to keep db
definitions in memory and avoid reloading them for each scan. Support for Freshclam and directly downloading custom
databases, both as AL-native update service. Both ClamAV and Freshclam can be fully configured.

### Comodo AV (abandoned)

A try to use the last version of Comodo AV engine for Linux. Because of the lack of pre-loading of the database,
it's been abandoned. It may still work, though.

### File Similarity

Comparing TLSH hashes to find similar files. It supports external lists in CSV as well as files badlisted in the
AssemblyLine system. Both are updated periodically, as native AL update services. Not recommended for use with large
number of badlisted files, it's just a linear comparison.

### Hashlookup

It performs hash lookups to identify well-known good and bad files. It be used to avoid analyzing well-known
files. Responses are cached. Currently supported services:
- [CIRCL Hashlookup](https://www.circl.lu/services/hashlookup/): identify well-known files and return trust
  score. DNS queries are used to check for the hash, and then REST API to get more details. It could be an
  online alternative to loading NIST NSRL database (and more) into Safelist service.
- [Cymru Malware Hash Registr](https://www.team-cymru.com/mhr): identify well-known malware files. Only
  DNS queries are used. This service does not offer extended details (e.g. no malware name).

### Network Information

Service to get information about IPs and domains. Currently supported:

 - IP data from MMDB files (you can configure your own, the default one is [GeoOpen](https://cra.circl.lu/opendata/geo-open/)),
 - WHOIS data for domains, including domains extracted from URIs. Results are cached.

Supported heuristics:
  - newly created domains (based on WHOIS data).

### PCAP Extractor

This service list TCP flows from a pcap file using Tshark. If supported by Tshark, it can also extract files.
It tries to set as much as possible tags, and respect safelisting to avoid unnecessary operations.

Supported heuristics:
 - external HTTP/non-HTTP connections,
 - data exfiltration threshold (based on total data sent out).

### Python Magic

Designed to help with analysis of Python artifacts. Currently supported:
- unpacking PyInstaller executables (using [pyinstxtractor-ng](https://github.com/pyinstxtractor/pyinstxtractor-ng)),
- decompyling Python bytecode (.pyc) (using [Decompyle++](https://github.com/zrax/pycdc)),
- extracting declared dependencies and matching them against configurable lists of suspicious and malicious packages,

### Simple Downloader

Very simple service to download URLs, without running a whole browser. User-agent can be configured.

## License

Although the code is licensed under the MIT license, the services may use third-party data or dependencies.
Please respect the applicable licenses.
