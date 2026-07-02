Name:           lsf
Version:        1.0.0
Release:        1%{?dist}
Summary:        Least Slack First terminal assignment scheduler
License:        MIT
URL:            https://github.com/suhao49/lsf
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  python3-build
BuildRequires:  python3-installer

Requires:       python3-textual
# tomli only needed on Python < 3.11
%if 0%{?python3_version_nodots} < 311
Requires:       python3-tomli
%endif

%description
lsf schedules assignments by urgency: priority divided by slack,
where slack is available working hours before the deadline minus
the risk-adjusted estimate. Uses a dynamic time-slice algorithm
to interleave tasks optimally across configured working windows.

Supports per-day working windows, configurable slice lengths,
breaks between sessions, and a panic mode that uses Earliest
Deadline First to triage overloaded schedules.

%prep
# Run from the repo root: rpmbuild -ba lsf.spec --define "_sourcedir %(pwd)"
# or use: fedpkg local / mock
cp -r %{_sourcedir}/. %{_builddir}/%{name}-%{version}

%build
cd %{_builddir}/%{name}-%{version}
python3 -m build --wheel --no-isolation

%install
cd %{_builddir}/%{name}-%{version}
python3 -m installer --destdir=%{buildroot} dist/*.whl

install -Dm644 config/config.toml.example \
    %{buildroot}%{_datadir}/%{name}/config.toml.example

install -Dm644 LICENSE \
    %{buildroot}%{_datadir}/licenses/%{name}/LICENSE

%files
%license LICENSE
%doc README.md
%{_bindir}/lsf
%{python3_sitelib}/lsf/
%{python3_sitelib}/lsf-%{version}.dist-info/
%{_datadir}/%{name}/

%changelog
* Sun Mar 22 2026 Nguyen <s02179@bvisvietnam.com> - 1.0.0-1
- Initial release
