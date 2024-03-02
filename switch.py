#!/usr/bin/python3
import sys
import struct
import wrapper
import threading
import time
from wrapper import recv_from_any_link, send_to_link, get_switch_mac, get_interface_name

LISTENING = 1
BLOCKING = 0
own_bridge = -1
root_bridge = -1
root_path_cost = 0
trunk_ports = dict()

def parse_ethernet_header(data):
    # Unpack the header fields from the byte array
    #dest_mac, src_mac, ethertype = struct.unpack('!6s6sH', data[:14])
    dest_mac = data[0:6]
    src_mac = data[6:12]
    
    # Extract ethertype. Under 802.1Q, this may be the bytes from the VLAN TAG
    ether_type = (data[12] << 8) + data[13]

    vlan_id = -1
    # Check for VLAN tag (0x8100 in network byte order is b'\x81\x00')
    if ether_type == 0x8200:
        vlan_tci = int.from_bytes(data[14:16], byteorder='big')
        vlan_id = vlan_tci & 0x0FFF  # extract the 12-bit VLAN ID
        ether_type = (data[16] << 8) + data[17]

    return dest_mac, src_mac, ether_type, vlan_id

def create_vlan_tag(vlan_id):
    # 0x8100 for the Ethertype for 802.1Q
    # vlan_id & 0x0FFF ensures that only the last 12 bits are used
    return struct.pack('!H', 0x8200) + struct.pack('!H', vlan_id & 0x0FFF)

def send_bdpu_every_sec():
    global root_bridge
    global own_bridge
    global trunk_ports
    while True:
        if own_bridge == root_bridge:
            for port in trunk_ports:
                root_bridge = own_bridge
                sender_bridge = own_bridge
                sender_path_cost = 0
                send_to_link(port, create_bpdu_packet(sender_bridge, sender_path_cost, port), 52)
        time.sleep(1)
        
                

def is_unicast(dest_mac):
    if dest_mac == 'ff:ff:ff:ff:ff:ff':
        return False
    return True

# 

def create_bpdu_packet(sender_bridge, sender_path_cost, t_port) -> bytes:
    global root_bridge
    data = struct.pack('!BBBBBB', 0x01, 0x80, 0xC2, 0x00, 0x00, 0x00) 
    data += get_switch_mac()
    data += struct.pack('!H', 38)
    data += struct.pack('!B', 0x42)
    data += struct.pack('!B', 0x42)
    data += struct.pack('!B', 0x03)
    data += struct.pack('!H', 0x00)
    data += struct.pack('!H', 0x00) 
    data += struct.pack('!q', root_bridge)
    data += struct.pack('!i', sender_path_cost)
    data += struct.pack('!q', sender_bridge)
    data += struct.pack('!H', t_port)
    data += struct.pack('!H', 1)
    data += struct.pack('!H', 20)
    data += struct.pack('!H', 2)
    data += struct.pack('!H', 15)
    return data 

def handle_bdpu_packet(data, interface):
    global root_bridge
    global own_bridge
    global root_path_cost

    data_slice = data[21:29]
    root_bridge_id = int.from_bytes(data_slice, byteorder='big')
    data_slice = data[29:33]
    root_cost_path = int.from_bytes(data_slice, byteorder='big')
    data_slice = data[33:41]
    sender_bridge = int.from_bytes(data_slice, byteorder='big')
    data_slice = data[41:43]
    t_port = int.from_bytes(data_slice, byteorder='big')
    
    # Verify if the switch is root bridge
    wasRootBridge = False
    if root_bridge == own_bridge:
        wasRootBridge = True
        
    # On receiving a BPDU:
    if root_bridge_id < root_bridge:
        root_bridge = root_bridge_id
        # Add 10 cost since all the links are 100 Mbps
        root_path_cost = root_cost_path + 10 
        root_port = interface

        # Set all interfaces not to hosts to blocking except the root port
        if wasRootBridge:
            for port in trunk_ports:
                if port != root_port:
                    trunk_ports[port] = BLOCKING
                    
          
        if trunk_ports[root_port] == BLOCKING:
            # Set root_port state to LISTENING
            trunk_ports[root_port] = LISTENING
            
 
        # Update and forward this BPDU to all other trunk ports with:
        data = data[:46] + bytes(own_bridge) + data[54:]
        data = data[:42] + bytes(root_path_cost) + data[46:]
        for port in trunk_ports:
            send_to_link(port, data, 38)
    elif root_bridge_id == root_bridge:
        if t_port == interface and root_cost_path + 10 < root_path_cost:
            root_path_cost = root_cost_path + 10
 
        elif t_port == interface:
            # Verify if the port should become designated.
            # Designated means the route from root is through
            # this switch. If we block the route, the other
            # switches will not be able to communicate with root bridge.
            # Note: Normally, the last BPDU should be saved 
            # from each port in order to calculate the designated port.
            if root_cost_path > root_path_cost:
                if trunk_ports[interface] == BLOCKING:
                    # Set port as the Designated Port and set state to LISTENING
                    trunk_ports[interface] = LISTENING
 
    elif sender_bridge == own_bridge:
        # Set port state to BLOCKING
        trunk_ports[interface] = BLOCKING
    else:
        return
 
    if own_bridge == root_bridge:
        for port in trunk_ports:
            # Set port as DESIGNATED_PORT
            trunk_ports[port] = LISTENING

def main():
    global LISTENING
    global BLOCKING
    global root_bridge
    global own_bridge
    global root_path_cost
    global trunk_ports
    # init returns the max interface number. Our interfaces
    # are 0, 1, 2, ..., init_ret value + 1
    switch_id = sys.argv[1]

    num_interfaces = wrapper.init(sys.argv[2:])
    interfaces = range(0, num_interfaces)

    # Create and start a new thread that deals with sending BDPU
    t = threading.Thread(target=send_bdpu_every_sec)
    t.start()

    MAC_Table = dict()
    Vlan_ids = dict()
    root_path_cost = 0
    index = 0
    
    # Initialize
    with open(f'configs/switch{switch_id}.cfg', 'r') as file:
        for line in file:
            if index == 0:
                own_bridge = int(line)
            else:
                Vlan_ids[interfaces[index - 1]] = ''.join(line.split(' ')[1].split('\n'))
                if Vlan_ids[interfaces[index - 1]] == 'T':
                    # add trunk port
                    trunk_ports[interfaces[index - 1]] = BLOCKING 
            index += 1
            
    root_bridge = own_bridge
    
    if own_bridge == root_bridge:
        for port in trunk_ports:
            trunk_ports[port] = LISTENING
    
    while True:
        # Note that data is of type bytes([...]).
        # b1 = bytes([72, 101, 108, 108, 111])  # "Hello"
        # b2 = bytes([32, 87, 111, 114, 108, 100])  # " World"
        # b3 = b1[0:2] + b[3:4].
        interface, data, length = recv_from_any_link()
        dest_mac, src_mac, ethertype, vlan_id = parse_ethernet_header(data)
        # Print the MAC src and MAC dst in human readable format
        dest_mac = ':'.join(f'{b:02x}' for b in dest_mac)
        src_mac = ':'.join(f'{b:02x}' for b in src_mac)

        # Note. Adding a VLAN tag can be as easy as
        # tagged_frame = data[0:12] + create_vlan_tag(10) + data[12:]
        
        # handle BDPU for STP protocol
        if dest_mac == '01:80:c2:00:00:00':
            handle_bdpu_packet(data, interface)
            continue
        
        # drop the packet if it happens to come from a blocked port
        if Vlan_ids[interface] == 'T':
            if trunk_ports[interface] == BLOCKING:
                continue
        
        # populate the CAM table
        MAC_Table[src_mac] = interface
        
        if is_unicast(dest_mac):
            # if the switch knows where to send it next
            if dest_mac in MAC_Table:
                # check if it is a trunk port
                if Vlan_ids[MAC_Table[dest_mac]] == 'T':
                    # check if it is not a blocked port
                    if trunk_ports[MAC_Table[dest_mac]] == LISTENING:
                        # check if the switch will forward the packet to a trunk port
                        if Vlan_ids[interface] == 'T':
                            send_to_link(MAC_Table[dest_mac], data, length)
                        else:
                            # create a tagged frame
                            new_data = data[:12] 
                            new_data += create_vlan_tag(int(Vlan_ids[interface]))
                            new_data += data[12:]
                            send_to_link(MAC_Table[dest_mac], new_data, length + 4)
                # check if the switch will forward the packet to an acces port from trunk
                elif int(Vlan_ids[MAC_Table[dest_mac]]) == vlan_id:
                    # remove tag
                    new_data = data[:12] + data[16:]
                    send_to_link(MAC_Table[dest_mac], new_data, length - 4)
                # check if the switch will forward from acces port to another acces port
                elif Vlan_ids[MAC_Table[dest_mac]] == Vlan_ids[interface]:
                    send_to_link(MAC_Table[dest_mac], data, length)
            else:
                # Flooding
                for o in interfaces:
                    if o != interface:
                        # check if it is a trunk port
                        if Vlan_ids[o] == 'T':
                            # check if it is not a blocked port
                            if trunk_ports[o] == LISTENING:
                                # check if the switch will forward the packet from a trunk port
                                if Vlan_ids[interface] == 'T':
                                    send_to_link(o, data, length)
                                else:
                                    # create a tagged frame
                                    new_data = data[:12] 
                                    new_data += create_vlan_tag(int(Vlan_ids[interface]))
                                    new_data += data[12:]
                                    send_to_link(o, new_data, length + 4)
                        # check if the switch will forward the packet to an acces port from trunk
                        elif Vlan_ids[interface] == 'T' and int(Vlan_ids[o]) == vlan_id:
                            new_data = data[:12] + data[16:]
                            send_to_link(o, new_data, length - 4)
                        # check if the switch will forward from acces port to another acces port
                        elif Vlan_ids[o] == Vlan_ids[interface]:
                            send_to_link(o, data, length)
        else:
            # Flooding
            for o in interfaces:
                    if o != interface:
                        # check if it is a trunk port
                        if Vlan_ids[o] == 'T':
                            # check if it is not a blocked port
                            if trunk_ports[o] == LISTENING:
                                # check if the switch will forward the packet from a trunk port
                                if Vlan_ids[interface] == 'T':
                                    send_to_link(o, data, length)
                                else:
                                    # create a tagged frame
                                    new_data = data[:12] 
                                    new_data += create_vlan_tag(int(Vlan_ids[interface])) 
                                    new_data += data[12:]
                                    send_to_link(o, new_data, length + 4)
                        # check if the switch will forward the packet to an acces port from trunk
                        elif Vlan_ids[interface] == 'T' and int(Vlan_ids[o]) == vlan_id:
                            # remove tag
                            new_data = data[:12] + data[16:]
                            send_to_link(o, new_data, length - 4)
                        # check if the switch will forward from acces port to another acces port
                        elif Vlan_ids[o] == Vlan_ids[interface]:
                            send_to_link(o, data, length)

if __name__ == "__main__":
    main()
