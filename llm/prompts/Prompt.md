Write a detailed specification to build a Frappe hosting platform called Atlas.

The purpose of Atlas is to be a solid building block for Frappe hosting. So, keep it very simple. 

Atlas will help users manage Firecracker MicroVMs. Nothing more. Site, bench management, IAM, and billing will be built separately.

I have already created the Frappe app atlas in the apps directory and installed it on atlas.local

Start with minimal requirements
- Atlas is a Frappe app that uses standard Frappe api and concepts; No extra UI. Desk is the UI
- Use DigitalOcean droplets as "metal nodes". We will change this later to actual metal machines
- Keep track of metal nodes and VMs
- Minimal Python script to bootstrap dependencies on Metal nodes
- SSH into the metal nodes to run VM management commands. Track these on Atlas. We'll build a CLI later
- Use the root user now. We'll use an unprivileged user later
- Spawn Ubuntu 24.04 VMs referenced in Firecracker docs
- systemd to run the VM. Keep them running.
- Filesystem-based image storage
- IPv6 only public networking. No private networking
- Maintain a directory structure for tracking VMs on metal nodes

Keep choices very, very simple to implement and understand. Use as few dependencies as possible. We will iteratively build the product and evolve the specification.

Write down the specification in the spec folder, and break it down into smaller files. Add wireframes or DocType layouts for UI.