import os
import signal
from sys import argv
import paramiko
import threading
import configparser
from lib.connections import select_server, get_servers
from lib.WorkspaceWatchDog import WorkspaceWatchDog
from lib.JournalSyncing import JournalSyncing
from watchdog.observers import Observer
from time import gmtime, strftime, sleep, time


def reconnect(jrnsync, reconnect_interval=30):
    while True:
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_pkey = paramiko.RSAKey.from_private_key_file(jrnsync.ssh_client_dict['pkey'])
            ssh_client.connect(hostname=jrnsync.ssh_client_dict['host'], username=jrnsync.ssh_client_dict['uname'],\
                               port=jrnsync.ssh_client_dict['port'], pkey=ssh_pkey)
            
            jrnsync.ssh_client_dict['connection'] = ssh_client
            jrnsync.sftp_client = jrnsync.ssh_client_dict['connection'].open_sftp()
            break
        except (paramiko.SSHException, IOError):
            print (f'Can\'t connect to Server {jrnsync.ssh_client_dict["host"]}')
        finally:
            sleep(reconnect_interval)


def sync(jrnsync, handler, sync_interval=30):
    while True:
        try:
            jrnsync.journal_syncing(handler.journal)
            handler.journal = handler.journal.iloc[0:0]
        except (paramiko.SSHException, IOError): #SSH session not active
            reconnect(jrnsync, reconnect_interval=30)
            continue
        finally:
            sleep(sync_interval)


'''
    las sync --all: Implicit Folder Sync (GOTO: workspace.ini > SYNC ALL PATHS)
    las sync --path WORKSPACE_PATH : Explicit Folder Sync

    las <PROCESS>: Sync all paths on workspace.ini and Execute <PROCESS> on Default Server.
    las --server SERVER_NAME --workspace WORKSPACE_NAME <PROCESS>: Sync selected workspace on workspace.ini and Execute <PROCESS> on Selected Server (SERVER_NAME must be on servers.ini).
'''


# TODO: Adjust it for the Generalized <las sync -s "SERVER" -w "WORKSPACE"> Command
if __name__ == '__main__':
    properties = configparser.ConfigParser()
    properties.read(os.path.join('.','config','props.ini'))
    properties = properties['properties']

    workspaces_config = configparser.ConfigParser()
    workspaces_config.read(os.path.join('.','config','workspaces.ini'))

    default_server = get_servers(os.path.join('.','config','servers.ini'), properties['default-server'])
    server_workspaces = workspaces_config[properties['default-server']]
    
    autosync  = True
    handlers  = {}
    observers = {}
    syncers   = {} 

    for workspace_name in server_workspaces:
        ## Get the Workspace Directory of SSH Client 
        workspace_path = server_workspaces[workspace_name]

        ## Instantiate a WorkspaceWatchDog WatchDog (handler)
        handlers[workspace_name] = WorkspaceWatchDog(local=True, verbose=True, workspace_name=workspace_name)
        ## Instantiate (and Start) a Recursive Observer Object for the given handler
        observers[workspace_name] = Observer()
        observers[workspace_name].schedule(handlers[workspace_name], path=workspace_path, recursive=True)
        observers[workspace_name].start()


    try:
        stdin, stdout, stderr = default_server['connection'].exec_command("echo $HOME")
        ssh_server_home_dir = stdout.readlines()[0].split('\n')[0]

        for workspace_name in server_workspaces:
                ## Get the Workspace Directory of SSH Client 
                workspace_path = server_workspaces[workspace_name]
                ## Instantiate a JournalSyncing (handler)
                jrnsync = JournalSyncing(default_server, workspace_path, ssh_server_home_dir, verbose=True, shallow_filecmp=True)
                syncers[workspace_name] = threading.Thread(target=sync, name=workspace_name, kwargs={'jrnsync':jrnsync, 'handler':handlers[workspace_name], 'sync_interval':30})
                syncers[workspace_name].start()
    except (paramiko.SSHException, IOError):
        print (f'Can\'t connect to Server {properties["default-server"]}')
        # reconnect() TODO


#TODO: Handle System Shutdown Gracefully
    try:
        while True:
            print(strftime("%Y-%m-%d %H:%M:%S", gmtime()), end='\r', flush=True)
            # signal.signal(signal.SIGTERM, raise KeyboardInterrupt) TODO
            sleep(1)
    except KeyboardInterrupt:
        print ('\nStopping the Observer...')
        for observer in observers:
            observers[observer].stop()
    finally:
        print ('Closing the SSH Connection...')
        for observer in observers:
            observers[observer].join()
        
        for syncer in syncers:
            syncers[syncer].join()

        default_server['connection'].close()