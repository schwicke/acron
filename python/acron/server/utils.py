#
# (C) Copyright 2019-2020 CERN
#
# This  software  is  distributed  under  the  terms  of  the  GNU  General  Public  Licence  version  3
# (GPL  Version 3), copied verbatim in the file "COPYING" /copied verbatim below.
#
# In applying this licence, CERN does not waive the privileges and immunities granted to it
# by virtue of its status as an Intergovernmental Organization or submit itself to any jurisdiction.
#
'''Server-side tools and functions'''

import functools
import inspect
import logging
import os
import re
from random import randint
from subprocess import Popen, PIPE
from socket import gethostbyaddr
from flask import current_app, request
import ldap3

from acron.constants import ReturnCodes
from acron.exceptions import KdestroyError, KinitError
from acron.utils import fqdnify as ext_fqdnify
from acron.utils import krb_init_keytab as ext_krb_init_keytab
from acron.utils import krb_destroy as ext_krb_destroy
from acron.server.constants import ConfigFilenames

__author__ = 'Philippe Ganz (CERN)'
__credits__ = ['Philippe Ganz (CERN)', 'Ulrich Schwickerath (CERN)',
               'Nacho Barrientos (CERN)', 'David Moreno Garcia (CERN)',
               'Rodrigo Bermudez Schettino (CERN)']
__maintainer__ = 'Rodrigo Bermudez Schettino (CERN)'
__email__ = 'rodrigo.bermudez.schettino@cern.ch'
__status__ = 'Development'


def dump_args(func):
    '''
    Dumps the functions parameters to the debug logger.

    :param func: the function to dump the args from
    :returns:    func with arguments dumping functionality added

    source: https://stackoverflow.com/a/6278457
    '''
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        '''
        Adds the args dump functionality.
        '''
        func_args = inspect.signature(func).bind(*args, **kwargs).arguments
        func_args_str = ', '.join('{} = {!r}'.format(*item)
                                  for item in func_args.items())
        logging.debug('%s.%s ( %s )', func.__module__,
                      func.__qualname__, func_args_str)
        return func(*args, **kwargs)
    return wrapper


# pylint: disable=R0914
@dump_args
def _ldap_groups_expansion(groups, server, base, user_regexp, group_regexp):
    '''
    Expands a group or list of groups.

    :param groups: group or list of groups
    :returns:      a set containing the users, empty if the group is empty
    '''
    if isinstance(groups, str):
        groups = [groups]
    elif ((isinstance(groups, list) and not all(isinstance(x, str) for x in groups))
          or not isinstance(groups, list)):
        raise ValueError('Please provide a string or a list of strings.')
    groups_to_process = list(set(groups))
    processed_groups = set()
    users = set()
    while groups_to_process:
        next_group = groups_to_process.pop(0)
        processed_groups.add(next_group)
        group_filter = '(&(objectClass=group)(CN=%s))' % next_group
        ldap_client = ldap3.Connection(server, auto_bind=True)
        ldap_client.search(base, group_filter, attributes=['member'])
        if ldap_client.entries and 'member' in ldap_client.entries[0]:
            for result in ldap_client.entries[0].member.values:
                user = re.match(user_regexp, result)
                group = re.match(group_regexp, result)
                if user is not None:
                    users.add('%s' % user.group(1))
                elif group is not None:
                    if group.group(1) not in processed_groups:
                        groups_to_process.append(group.group(1))
    ldap_client.unbind()
    return users


@dump_args
def ldap_groups_expansion(groups):
    '''
    Expands a group or list of groups.

    :param groups: group or list of groups
    :returns:      a set containing the users, empty if the group is empty
    '''
    users = _ldap_groups_expansion(groups,
                                   current_app.config['LDAP_SERVER'],
                                   current_app.config['LDAP_BASE'],
                                   current_app.config['LDAP_USER_REGEXP'],
                                   current_app.config['LDAP_GROUP_REGEXP'])
    return users


@dump_args
def check_ldap_group_membership(user, ldap_group):
    '''
    Check if the user is in the specified LDAP group.

    :param user:       user to perform lookup
    :param ldap_group: name of LDAP group
    :returns:          boolean, True if user is in given LDAP group, False otherwise.
    '''
    ldap_group_users = ldap_groups_expansion(ldap_group)

    logging.debug(
        f'Checking LDAP group membership of user {user} in group {ldap_group}')

    is_user_in_ldap_group = user in ldap_group_users
    logging.debug(f'User {user} in {ldap_group}: {is_user_in_ldap_group}')
    return is_user_in_ldap_group


@dump_args
def get_remote_hostname():
    '''
    Performs a DNS lookup on remote_addr of the current context.

    :returns:            the hostname corresponding to request's remote host
    :raises SystemError: raises an exception if the process call fails
    '''
    lookup = gethostbyaddr(request.remote_addr)
    return lookup[0] + '(' + request.remote_addr + ')'


@dump_args
def default_log_line_request():
    '''
    Generates a formatted string for logging on requests.

    :returns: a formatted string
    '''
    return 'User {username} ({host}) requests {method}'.format(
        username=request.remote_user, host=get_remote_hostname(), method=request.method)


@dump_args
def fqdnify(hostname):
    '''
    Make sure a hostname is an FQDN.

    :param host: hostname to check
    :returns:    the same host if already an FQDN or the hostname with the domain appended if not
    '''
    return ext_fqdnify(hostname, current_app.config['DOMAIN'])


@dump_args
def krb_init_keytab(keytab, principal):
    '''
    Request a Kerberos TGT with the given keytab and principal.

    :param keytab:      the keytab to use
    :param principal:   the principal to use with the keytab
    :returns:           OK if the initialization succeeded
    :raises KinitError: raises an exception if the initialization failed
    '''
    try:
        ext_krb_init_keytab(keytab, principal)
    except KinitError as error:
        logging.debug('Kerberos initialization with keytab failed. %s', error)
        raise KinitError from error
    return ReturnCodes.OK


@dump_args
def krb_destroy(cachefile):
    '''
    Delete the Kerberos TGT.

    :raises KdestroyError: raises an exception if the destruction failed
    '''
    try:
        ext_krb_destroy(cachefile)
    except KdestroyError as error:
        logging.debug('Kerberos destruction failed. %s', error)
        raise KdestroyError from error
    return ReturnCodes.OK


@dump_args
def create_parent(path):
    '''
    Check if the parent directory exists and create it if not.

    :param path: path of the leaf object
    '''
    path_parent = os.path.abspath(os.path.join(path, os.pardir))
    if not os.path.exists(path_parent):
        logging.debug('%s does not exist, creating.', path_parent)
        os.makedirs(path_parent, 0o0775)


@dump_args
def _cron2quartz(schedule):
    '''
    Convert cron schedule to quartz

    :param schedule: Schedule in cron format
    :returns: Schedule in quartz format
    '''
    fields = re.split(r'\s+', schedule)
    if fields[2] == '*':
        if fields[4] == '*':
            # flaw: if both all monthdays and all weekdays are specified, weekday should be?
            fields[4] = '?'
        else:
            if fields[4] != '?':
                fields[2] = '?'

    # we fix the year to be '*' and the second to be random between 10s
    return ' '.join([str(randint(0, 10))] + fields + ['*'])


@dump_args
def _execute_command(cmd):
    '''
    Open subprocess and execute command

    :param cmd: Command to execute as string
    :returns: Tuple of return code and error message, if any
    '''
    logging.debug('Popen: %s', cmd)
    with Popen([cmd],
               universal_newlines=True,
               stdout=PIPE,
               stderr=PIPE,
               shell=True) as process:
        out, err = process.communicate()
        logging.debug(out.rstrip('\n'))

    if process.returncode != 0:
        logging.error(err)

    return process.returncode, out, err


@dump_args
def _get_project_home_path(config, project_id, filename):
    '''
    Get path to shareable file in a specific project

    :param config:     a dictionary containing all the config values
    :param project_id: ID of project to construct path with
    :param filename:   name of file to get path to

    :returns: path to file for project_id
    :returns: absolute path to parent directory of file for project_id
    '''
    path = os.path.join(config['SCHEDULER']
                        ['PROJECTS_HOME'], project_id, filename)
    path_parent = os.path.abspath(os.path.join(path, os.pardir))

    return path, path_parent


@dump_args
def _delete_shareable_file(project_id, config):
    '''
    Delete shareable file of project

    :param project_id: ID of project to delete shareable file
    :param config:                a dictionary containing all the config values
    :returns:          Boolean True if file existed
    '''
    path, _ = _get_project_home_path(config,
                                     project_id,
                                     ConfigFilenames.SHAREABLE)

    msg = f'project {project_id} under {path}'

    if not os.path.exists(path):
        logging.debug(
            f'No shareable file to delete for {msg}')
        return False

    logging.debug(
        f'Deleting shareable file of {msg}')
    os.unlink(path)
    return True
