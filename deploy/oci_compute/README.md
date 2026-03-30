This directory contains artifacts and step-by-step instructions to deploy the NeuroStamp app on an Oracle Cloud Infrastructure (OCI) Always Free Compute instance (VM).

Overview:
- Provision an OCI Always Free Ampere or VM.Standard.E2.1.Micro compute instance.
- Attach a block volume (optional) for persistence of uploaded files and the SQLite database.
- Install Python3.11, create a virtualenv, install requirements, and run the app using Gunicorn with Uvicorn workers behind Nginx and systemd.
- Configure TLS using Let's Encrypt certbot (optional but recommended for production).

Files:
- `setup_vm.sh` - Bash script with commands to run on the VM to set up the environment, install dependencies, create a service, and configure Nginx.
- `neurostamp.service` - systemd unit file to run Gunicorn / Uvicorn.
- `nginx_neurostamp.conf` - Nginx server block to reverse-proxy to Gunicorn and serve static files.

Usage:
1. Provision an OCI Always Free compute instance (Ubuntu 22.04 recommended).
2. SSH into the instance as `opc` or the user you created.
3. Upload the NeuroStamp project directory (git clone the repo) into `/home/ubuntu/neurostamp` or a path of your choosing.
4. Copy `setup_vm.sh`, `neurostamp.service`, and `nginx_neurostamp.conf` to the VM and follow the script instructions.

Notes:
- The script assumes the project will be placed in `/home/ubuntu/NeuroStamp` and the Python version is 3.11. Adjust paths as needed.
- The script uses `python3.11` and `pip` names consistent with Ubuntu 22.04 or newer images where Python 3.11 is available. If not available, install from deadsnakes or use the default python3 and adjust virtualenv path.
- This deployment keeps the existing SQLite database (`neurostamp.db`) and `static/uploads` on the VM filesystem. Back them up regularly.
