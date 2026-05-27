The e2e tests and unit tests claim to cover a lot of surface. I found it very hard to set up the app on a Fresh site.

- Not sure where to start. Later found out that Server Provider is where you start. Unsure if this is true, though.
- Not sure what's needed to set up the Server.
- Not sure what fields to populate in Virtual Machine Image.
- Not sure what's needed to create a VM.

To solve these problems. Create a bootstrap script that'll create the needed documents. So user can quickly get a Server provisioned and start a VM on this server. 

You tend to make the installer scripts very complicated. Keep it very, very simple. Use standard Frappe calls. Only use public methods from each class. Note down issues while following these instructions.

About networking. If the address for the Server is 2400:6180:100:d0:0:1:4ae1:d001 then on DigitalOcean /124 means 2400:6180:0100:00d0:0000:0001:4ae1:d000/124 But the ipv6_prefix and ipv6_virtual_machine_range fields shows wrong subnets. 

I'm unable to connect to the VM over the internet
- Address of the VM isn't shown on the Virtual Machine DocType.
- The tap interface doesn't seem to have any address associated.