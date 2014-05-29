#
# Description: Publishes container network information using docker API
# Name: John Yi
# Version: 0.1.0

import docker
import etcd
import socket
import string
import sys
import json
import os
import pprint
import re
import time
import threading


class DockerSocket(threading.Thread):

    ''' Using a Unix Domain Socket just returns the event strings
        events as json. 
    '''

    def __init__(self, url, call_back):
        super(DockerSocket, self).__init__()
        self.events_store = {}
        self.call_back = call_back
        self.docker_client = docker.Client(url)
        if re.search(r'unix://', url):
            self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            socket_file_path = string.replace(url, r'unix://', '')
            self.s.connect( socket_file_path )
            self.s.send("GET /events HTTP/1.1\n\n")
            self.file_interface = self.s.makefile()
        else:
            print "Only support Unix Domain Sockets currently."
            sys.exit(0) 

    def _load_running_containers( self ):
        for container in self.docker_client.containers():
            # transform containers to contain keys that events
            # have
            container['id'] = container['Id']
            container['status'] = 'up'
            event = Event( container, self.docker_client ) 
            self.events_store[container['id']] = event

    def run(self):
        self._load_running_containers()
        while ( True ):
            line = self.file_interface.readline()
            
            #
            # Will find all events need to be stored because
            # they could be also events that deleted the information
            #
            if re.search('^\{.*\}', line): # will find all events
                try:
                    event_dict = json.loads(line.strip())
                    pprint.pprint("{0}".format(event_dict))

                    if event_dict['status'] == 'destroy':
                        event = self.events_store.pop(event_dict['id'])
                    elif event_dict['status'] == 'create':
                        event = Event( event_dict, self.docker_client )
                        self.events_store[event_dict['id']] = event
                    else:
                        event = Event( event_dict, self.docker_client )

                    self.call_back(event)
                except docker.APIError, e:
                    print "docker.APIError: {0}".format(e)
                    continue 
                except KeyError, e:
                    print "KeyError: {0}".format(e)
                    continue
                except:
                    # Make sure we close the socket if a exception causes us to break out
                    # of the loop
                    print "Unexpected error:", sys.exc_info()[0]
                    print "Closing socket..."
                    self.s.close()
                    raise


class Event(object):

    ''' Docker specific Event object that uses docker client
        found here: https://github.com/jplana/python-etcd.git 
    '''

    def __init__(self, event, docker_client):
        self.event = event
        self.client = docker_client
        self.container_info = self.client.inspect_container(event['id']) 

    @property
    def status(self):
        return self.event['status']

    @property
    def id(self):
        return self.event['id']

    @property
    def location(self):
        for env_variable in self.container_info['Config']['Env']:
            if re.search('ETCD_SERVICES', env_variable):
                return os.path.join('/services', \
                                string.lstrip( string.split( env_variable, '=' )[1].strip(), '/'), \
                                string.lstrip( self.container_info['Name'], '/'))
        return ''

    @property
    def node(self):
        return self.container_info['HostConfig']['PortBindings']


class EventManager(object):

    ''' Setup event states to take action on.

        event_entry: { <event_status> : <class_instance_method> }
        example event_entry: {  'state' : register.publish } where register.publish is the
                                 method that get's called to start an action

    ''' 

    def __init__(self, event_map={}):
        self.event_map = event_map

    def add_event(self, event_entry):
        self.event_map.update( event_entry )

    def remove_event(self, event_status):
        return self.event_map.pop( event_status )

    def event_action(self, event):
        if event.status in self.event_map:
            # event_map[event.status] should refer to a method to execute
            # also use the event as an argument
            print "DEBUG: {0}".format(event.status)
            self.event_map[event.status](event.location, json.dumps(event.node))


class Register(threading.Thread):

    ''' 
        Publishes and deletes entries in the Registry.
    '''

    def __init__(self, registry_client, ttl=0, refresh_dict={}):
        super(Register, self).__init__()
        self.refresh_dict = refresh_dict
        self.client = registry_client
        self.ttl = ttl

    def publish( self, location, node ):
        try:
            if location:
                print "publish entry: {0}".format(location)
                print "config info: {0}".format(node)
                self.refresh_dict[location] = { "timer" : threading.Timer(self.ttl, self.refresh, (location, node)),
                                                "args" : ( location, node ) }
                self.refresh_dict[location]["timer"].start()
                return self.client.write(location, node, self.ttl)
            else:
                return None
        except etcd.EtcdException, e:
            print "etcd.EtcdException: {0}".format(e)

    def delete( self, location, node ):
        try:
            if location:
                print "delete entry: {0}".format(location)
                t = self.refresh_dict.pop(location)
                t["timer"].cancel() # cancel the refresh
                return self.client.delete(location)
            else:
                return None
        except etcd.EtcdException, e:
            print "etcd.EtcdException: {0}".format(e)
            

    def refresh( self, location, node ):
        if location:
            print "refresh entry: {0}".format(location)
            print "ttl: {0}".format(self.ttl) 
            return self.client.write(location, node, self.ttl)
        else:
            return None

    def run( self ):
        while ( True ):
            for location in self.refresh_dict:
                if self.refresh_dict[location]["timer"].finished.is_set():
                    self.refresh_dict[location]["timer"] = threading.Timer(   self.ttl, self.refresh, \
                                                                    self.refresh_dict[location]["args"]   )
                    self.refresh_dict[location]["timer"].start()
            time.sleep(1)


def publish_running_containers(register, base_url="unix:///tmp/docker.sock"):
    dc = docker.Client(base_url=base_url)
    for container in dc.containers():
        # add some additional keys in the dict so they can
        # be loaded as an event
        container['id'] = container['Id']
        container['status'] = 'up'
        e = Event( container, dc )
        r = register.publish( e.location, e.node )
        if r:
            print "Published: {0}".format(r)
        else:
            print "Nothing to publish"

def main():
    #
    # create etcd client: 10.1.42.1 is the docker0 interface for
    # all docker instances
    #
    etcd_client = etcd.Client(host='10.1.42.1')

    #
    # create register object
    #
    register = Register(etcd_client, 60)
    # register = Register(etcd_client)

    #
    # publish config info for existing containers
    #
    publish_running_containers( register )

    #
    # create event manager object
    #
    event_manager = EventManager()
    event_manager.add_event( { 'start' : register.publish } )
    event_manager.add_event( { 'die' : register.delete } )

    #
    # create docker socket
    #
    docker_socket = DockerSocket('unix:///tmp/docker.sock', event_manager.event_action) 

    #
    # start event listening
    #
    docker_socket.daemon = True
    docker_socket.start()

    #
    # start ttl refresh daemon
    #
    register.daemon = True
    register.start()

    #
    # refresh nodes
    #
    while ( True ):
        time.sleep(10)
        pass



if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print "Ctrl-C executed exiting..."
