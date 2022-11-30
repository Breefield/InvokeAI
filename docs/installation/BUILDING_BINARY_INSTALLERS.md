# How to build "binary" installers (InvokeAI-mac/windows/linux.zip)

## 1. Ensure `installers/requirements.in` is correct and up to date on the branch to be installed.

## <a name="step-2"></a> 2. Run `pip-compile` on each platform.

On each target platform, in the branch that is to be installed, and inside the InvokeAI git root folder, run the following commands:
```commandline
conda activate invokeai # or however you activate python
pip install pip-tools
pip-compile --allow-unsafe --generate-hashes --output-file=installer/<reqsfile>.txt installer/requirements.in
```
where `<reqsfile>.txt` is whichever of
```commandline
py3.10-darwin-arm64-mps-reqs.txt
py3.10-darwin-x86_64-reqs.txt
py3.10-linux-x86_64-cuda-reqs.txt
py3.10-windows-x86_64-cuda-reqs.txt
```
matches the current OS and architecture. 
> There is no way to cross-compile these. They must be done on a system matching the target OS and arch.

## <a name="step-3"></a> 3. Set github repository and branch 

Once all reqs files have been collected and committed **to the branch to be installed**, edit `installer/install.sh` so that `RELEASE_URL` and `RELEASE_SOURCEBALL` point to the github repo and branch that is to be installed. 

For example, to install `main` branch of `InvokeAI`, they should be set as follows:

```commandline
RELEASE_URL=https://github.com/invoke-ai/InvokeAI
RELEASE_SOURCEBALL=/archive/refs/heads/main.tar.gz
```

Or, to install `damians-cool-feature` branch of `damian0815`, set them as follows:

```commandline
RELEASE_URL=https://github.com/damian0815/InvokeAI
RELEASE_SOURCEBALL=/archive/refs/heads/damians-cool-feature.tar.gz
```

The branch and repo specified here **must** contain the correct reqs files. The installer zip files **do not** contain requirements files, they are pulled from the specified branch during the installation process.

## 4. Create zip files.

cd into the `installers/` folder and run `./create_installers.sh`. This will create `InvokeAI-mac.zip`, `InvokeAI-windows.zip` and `InvokeAI-linux.zip`. These files can be distributed to end users, who should unzip them and run the .bat /.sh file to install the branch set in [step 3](#step-3) from the repo also set in step 3.

These zips will continue to funciont as installers for all future pushes to those branches, as long as necessary changes to `requirements.in` are propagated in a timely manner to the `py3.10-*-reqs.txt` files using pip-compile as outlined in [step 2](#step-2).
