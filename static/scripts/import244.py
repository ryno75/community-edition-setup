#!/usr/bin/python

# Requires JSON Merge library
# wget https://github.com/avian2/jsonmerge/archive/master.zip
# unzip master.zip
# cd jsonmerge-master
# python setup.py install

# Also requires ldif.py in same folder

import os
import os.path
import shutil
import sys
import traceback
from ldif import LDIFParser, LDIFWriter
from jsonmerge import merge
import json
import tempfile
import logging
import filecmp

password_file = tempfile.mkstemp()[1]
backup24_folder = None
backup_version = None
current_version = None
hostname = None

service = "/usr/sbin/service"
ldapmodify = "/opt/opendj/bin/ldapmodify"
ldapsearch = "/opt/opendj/bin/ldapsearch"
ldapdelete = "/opt/opendj/bin/ldapdelete"
ldif_import = "/opt/opendj/bin/import-ldif"
ldif_export = "/opt/opendj/bin/export-ldif"
keytool = '/usr/bin/keytool'
key_store = '/usr/java/latest/lib/security/cacerts'
os_types = ['centos', 'redhat', 'fedora', 'ubuntu', 'debian']

ignore_files = ['101-ox.ldif',
                'gluuImportPerson.properties',
                'oxTrust.properties',
                'oxauth-config.xml',
                'oxauth-errors.json',
                'oxauth.config.reload',
                'oxauth-static-conf.json',
                'oxtrust.config.reload',
                'config.ldif',
                ]

ldap_creds = ['-h', 'localhost',
              '-p', '1636', '-Z', '-X',
              '-D', '"cn=directory manager"',
              '-j', password_file
              ]

# configure logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)-8s %(name)s %(message)s',
                    filename='import_244.log',
                    filemode='w')
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('%(levelname)-8s %(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)
logging.getLogger('jsonmerge').setLevel(logging.WARNING)


class MyLDIF(LDIFParser):
    def __init__(self, input, output):
        LDIFParser.__init__(self, input)
        self.targetDN = None
        self.targetAttr = None
        self.targetEntry = None
        self.DNs = []
        self.lastDN = None
        self.lastEntry = None

    def getResults(self):
        return (self.targetDN, self.targetAttr)

    def getDNs(self):
        return self.DNs

    def getLastEntry(self):
        return self.lastEntry

    def handle(self, dn, entry):
        if self.targetDN is None:
            self.targetDN = dn
        self.lastDN = dn
        self.DNs.append(dn)
        self.lastEntry = entry
        if dn.lower().strip() == self.targetDN.lower().strip():
            self.targetEntry = entry
            if self.targetAttr in entry:
                self.targetAttr = entry[self.targetAttr]


def copyFiles(backup24_folder):
    logging.info('Copying backup files from /etc, /opt and /usr')
    os.path.walk("%s/etc" % backup24_folder, walk_function, None)
    os.path.walk("%s/opt" % backup24_folder, walk_function, None)
    os.path.walk("%s/usr" % backup24_folder, walk_function, None)


def getOldEntryMap(folder):
    files = os.listdir(folder)
    dnMap = {}

    # get the new admin DN
    admin_dn = getDns('/opt/opendj/ldif/people.ldif')[0]

    for fn in files:
        # oxIDPAuthentication in appliance.ldif file  in 2.3 is incompatible
        # with the gluu-server version > 2.4. Hence skip the file
        if 'appliance' in fn and backup_version < 240:
            continue
        dnList = getDns("%s/%s" % (folder, fn))
        for dn in dnList:
            # skip the entry of Admin DN and its leaves
            if fn == 'people.ldif' and admin_dn in dn:
                continue
            dnMap[dn] = fn
    return dnMap


def getEntry(fn, dn):
    parser = MyLDIF(open(fn, 'rb'), sys.stdout)
    parser.targetDN = dn
    parser.parse()
    return parser.targetEntry


def getDns(fn):
    parser = MyLDIF(open(fn, 'rb'), sys.stdout)
    parser.parse()
    return parser.DNs


def getOutput(args):
        try:
            logging.debug("Running command : %s" % " ".join(args))
            output = os.popen(" ".join(args)).read().strip()
            return output
        except:
            logging.error("Error running command : %s" % " ".join(args))
            logging.error(traceback.format_exc())
            sys.exit(1)


def startOpenDJ():
    logging.info('Starting Directory Server ...')
    output = getOutput([service, 'opendj', 'start'])
    if output.find("Directory Server has started successfully") > 0:
        logging.info("Directory Server has started successfully")
    else:
        logging.critical("OpenDJ did not start properly... exiting."
                         " Check /opt/opendj/logs/errors")
        sys.exit(2)


def stopOpenDJ():
    logging.info('Stopping Directory Server ...')
    output = getOutput([service, 'opendj', 'stop'])
    if output.find("Directory Server is now stopped") > 0:
        logging.info("Directory Server is now stopped")
    else:
        logging.critical("OpenDJ did not stop properly... exiting."
                         " Check /opt/opendj/logs/errors")
        sys.exit(3)


def walk_function(a, directory, files):
    # Skip copying the openDJ config from older versions to 2.4.3
    if current_version >= 243:
        ignore_folders = ['opendj', 'template', 'endorsed']
        for folder in ignore_folders:
            if folder in directory:
                return

    for f in files:
        if f in ignore_files:
            continue
        fn = "%s/%s" % (directory, f)
        targetFn = fn.replace(backup24_folder, '')
        if os.path.isdir(fn):
            if not os.path.exists(targetFn):
                os.mkdir(targetFn)
        else:
            # It's a file...
            try:
                logging.debug("copying %s", targetFn)
                shutil.copyfile(fn, targetFn)
            except:
                logging.error("Error copying %s", targetFn)


def stopTomcat():
    logging.info('Stopping Tomcat ...')
    output = getOutput([service, 'tomcat', 'stop'])
    logging.debug(output)


def startTomcat():
    logging.info('Starting Tomcat ...')
    output = getOutput([service, 'tomcat', 'start'])
    logging.debug(output)


def preparePasswordFile():
    # prepare password_file
    with open('/install/community-edition-setup/setup.properties.last', 'r') \
            as sfile:
        for line in sfile:
            if 'ldapPass=' in line:
                with open(password_file, 'w') as pfile:
                    pfile.write(line.split('=')[-1])
                break


def getCurrentVersion():
    with open('/opt/tomcat/webapps/oxauth/META-INF/MANIFEST.MF', 'r') as f:
        for line in f:
            if 'Implementation-Version' in line:
                return int(line.split(':')[-1].replace('.', '').strip()[:3])


def getBackupProperty(prop):
    with open(os.path.join(backup24_folder, 'setup.properties'), 'r') as f:
        for line in f:
            if '{0}='.format(prop) in line:
                return line.split('=')[-1].strip()


def setPermissions():
    logging.info("Changing ownership")
    realCertFolder = os.path.realpath('/etc/certs')
    realTomcatFolder = os.path.realpath('/opt/tomcat')
    realLdapBaseFolder = os.path.realpath('/opt/opendj')
    realIdpFolder = os.path.realpath('/opt/idp')
    realOxBaseFolder = os.path.realpath('/var/ox')

    getOutput(['/bin/chown', '-R', 'tomcat:tomcat', realCertFolder])
    getOutput(['/bin/chown', '-R', 'tomcat:tomcat', realTomcatFolder])
    getOutput(['/bin/chown', '-R', 'ldap:ldap', realLdapBaseFolder])
    getOutput(['/bin/chown', '-R', 'tomcat:tomcat', realOxBaseFolder])
    getOutput(['/bin/chown', '-R', 'tomcat:tomcat', realIdpFolder])


def getOsType():
    try:
        with open('/etc/redhat-release', 'r') as f:
            contents = f.read()
    except IOError:
        with open('/etc/os-release', 'r') as f:
            contents = f.read()

    if 'CentOS' in contents:
        return os_types[0]
    elif 'Red Hat' in contents:
        return os_types[1]
    elif 'Ubuntu' in contents:
        return os_types[3]
    elif 'Debian' in contents:
        return os_types[4]


def updateCertKeystore():
    global key_store
    logging.info('Updating the SSL Keystore')
    keys = ['httpd', 'asimba', 'shibIDP', 'opendj']

    # The old opendj.crt file is useless. export the file from the
    # new opendj truststore and import that into the java truststore
    openDjPinFn = '/opt/opendj/config/keystore.pin'
    openDjTruststore = '/opt/opendj/config/truststore'
    openDjPin = None

    if getOsType() in ['debian', 'ubuntu']:
        key_store = '/etc/ssl/certs/java/cacerts'

    try:
        f = open(openDjPinFn)
        openDjPin = f.read().splitlines()[0]
        f.close()
    except Exception as e:
        logging.error("Reading OpenDJ truststore failed. Without OpenDJ cert"
                      " the import is useless")
        logging.debug(e)
        sys.exit(1)

    # Export public OpenDJ certificate
    logging.debug("Exporting OpenDJ certificate")
    result = getOutput([keytool, '-exportcert', '-keystore', openDjTruststore,
                        '-storepass', openDjPin,
                        '-file', '/etc/certs/opendj.crt',
                        '-alias', 'server-cert', '-rfc'])
    logging.debug(result)

    # import all the keys into the keystore
    for key in keys:
        alias = "{0}_{1}".format(hostname, key)
        filename = "/etc/certs/{0}.crt".format(key)
        # delete the old key from the keystore
        logging.debug('Deleting new %s', alias)
        result = getOutput([keytool, '-delete', '-alias', alias, '-keystore',
                            key_store, '-storepass', 'changeit',
                            '-noprompt'])
        if 'error' in result:
            logging.error(result)
        else:
            logging.debug('Delete operation success.')
        # import the new key into the keystore
        logging.debug('Importing old %s', alias)
        result = getOutput([keytool, '-import', '-trustcacerts', '-file',
                            filename, '-alias', alias, '-keystore',
                            key_store, '-storepass', 'changeit',
                            '-noprompt'])
        if 'error' in result:
            logging.error(result)
        else:
            logging.debug('Certificate import success.')


def processPeople(ldif_folder):
    logging.info('Updating oxSectorIdentifierURI to oxSectorIdentifier in' +
                 ' people.ldif')
    peopleldif = os.path.join(ldif_folder, 'people.ldif')
    new_peopleldif = os.path.join(ldif_folder, 'people_updated.ldif')

    with open(peopleldif, 'r') as infile:
        with open(new_peopleldif, 'w') as outfile:
            for line in infile:
                if 'oxSectorIdentifierURI' in line:
                    line.replace('oxSectorIdentifierURI', 'oxSectorIdentifier')
                outfile.write(line)
    shutil.move(new_peopleldif, peopleldif)


def importLDIF(folder):
    ldif_file = os.path.join(folder, 'processed.ldif')
    logging.info("Running ldif-import on %s", ldif_file)
    command = [ldif_import, '-n', 'userRoot',
               '-l', ldif_file, '-R', ldif_file+'.rejects']
    output = getOutput(command)
    logging.debug(output)


def exportLDIF(folder):
    logging.info('Exporting the current LDAP data')
    current = os.path.join(folder, 'current.ldif')
    command = [ldif_export, '-n', 'userRoot', '-l', current]
    output = getOutput(command)
    logging.debug(output)


def processLDIF(backupFolder, newFolder):
    logging.info('Processing the LDIF data')
    current_ldif = os.path.join(newFolder, 'current.ldif')
    currentDNs = getDns(current_ldif)

    processed_ldif = open(os.path.join(newFolder, 'processed.ldif'), 'w')
    ldif_writer = LDIFWriter(processed_ldif)

    ignoreList = ['objectClass', 'ou', 'oxAuthJwks', 'oxAuthConfWebKeys']
    old_dn_map = getOldEntryMap(backupFolder)

    multivalueAttrs = ['oxTrustEmail', 'oxTrustPhoneValue', 'oxTrustImsValue',
                       'oxTrustPhotos', 'oxTrustAddresses', 'oxTrustRole',
                       'oxTrustEntitlements', 'oxTrustx509Certificate']
    # Rewriting all the new DNs in the new installation to ldif file
    for dn in currentDNs:
        new_entry = getEntry(current_ldif, dn)
        if dn not in old_dn_map.keys():
            #  Write directly to the file if there is no matching old DN data
            ldif_writer.unparse(dn, new_entry)
            continue

        old_entry = getEntry(os.path.join(backupFolder, old_dn_map[dn]), dn)
        for attr in old_entry.keys():
            if attr in ignoreList:
                continue

            if attr not in new_entry:
                new_entry[attr] = old_entry[attr]
            elif old_entry[attr] != new_entry[attr]:
                if len(old_entry[attr]) == 1:
                    try:
                        old_json = json.loads(old_entry[attr][0])
                        new_json = json.loads(new_entry[attr][0])
                        new_json = merge(new_json, old_json)
                        new_entry[attr] = [json.dumps(new_json)]
                    except:
                        new_entry[attr] = old_entry[attr]
                        logging.debug("Keeping old value for %s", attr)
                else:
                    new_entry[attr] = old_entry[attr]
                    logging.debug("Keep multiple old values for %s", attr)
        ldif_writer.unparse(dn, new_entry)

    # Pick all the left out DNs from the old DN map and write them to the LDIF
    for dn in sorted(old_dn_map, key=len):
        if dn in currentDNs:
            continue  # Already processed

        entry = getEntry(os.path.join(backupFolder, old_dn_map[dn]), dn)

        for attr in entry.keys():
            if attr not in multivalueAttrs:
                continue  # skip conversion

            attr_values = []
            for val in entry[attr]:
                json_value = None
                try:
                    json_value = json.loads(val)
                    if type(json_value) is list:
                        attr_values.extend([json.dumps(v) for v in json_value])
                except:
                    loggin.debug('Cannot parse multival %s in DN %s', attr, dn)
                    attr_values.append(val)
            entry[attr] = attr_values

        ldif_writer.unparse(dn, entry)

    # Finally
    processed_ldif.close()


def copyCustomLDAPSchema(backup):
    logging.info("Copying the Custom LDAP Schema")
    backup_schema_dir = os.path.join(backup, 'opt/opendj/config/schema/')
    present_schema_dir = '/opt/opendj/config/schema/'
    custom_files = ['100-user.ldif', '99-user.ldif']
    for cf in custom_files:
        if os.path.isfile(os.path.join(backup_schema_dir, cf)):
            shutil.copyfile(
                os.path.join(backup_schema_dir, cf),
                os.path.join(present_schema_dir, cf)
            )

    # Copy the extra files like user created custom schema files
    diff = filecmp.dircmp(backup_schema_dir, present_schema_dir)
    for ldif_file in diff.left_only:
        shutil.copyfile(
            os.path.join(backup_schema_dir, ldif_file),
            os.path.join(present_schema_dir, ldif_file)
        )


def main(folder_name):
    global backup24_folder, backup_version, current_version, service, hostname

    # Verify that all required folders are present
    backup24_folder = folder_name
    if not os.path.exists(backup24_folder):
        logging.critical("Backup folder %s does not exist.", backup24_folder)
        sys.exit(1)

    etc_folder = os.path.join(backup24_folder, 'etc')
    opt_folder = os.path.join(backup24_folder, 'opt')
    ldif_folder = os.path.join(backup24_folder, 'ldif')

    if not (os.path.exists(etc_folder) and os.path.exists(opt_folder) and
            os.path.exists(ldif_folder)):
        logging.critical("Backup folder doesn't have all the information."
                         " Rerun export.")
        sys.exit(1)

    # Identify the version of the backup and installation
    backup_version = int(getBackupProperty('version').replace('.', '').strip()[:3])
    current_version = getCurrentVersion()

    hostname = getBackupProperty('hostname')

    # some version specific adjustment
    if current_version >= 243 and backup_version < 243:
        skip_files = ['oxauth.xml',  # /opt/tomcat/conf/Catalina/localhost
                      'oxasimba-ldap.properties',
                      'oxauth-ldap.properties',
                      'oxidp-ldap.properties',
                      'oxtrust-ldap.properties',  # /opt/tomcat/conf
                      'gluuTomcatWrapper.conf',
                      'catalina.properties',
                      'oxTrustLdap.properties',  # from 2.3.6
                      ]
        global ignore_files
        ignore_files += skip_files

    outputFolder = "./output_ldif"
    outputLdifFolder = "%s/config" % outputFolder
    # newLdif = "%s/current_config.ldif" % outputFolder

    if not os.path.exists(outputFolder):
        os.mkdir(outputFolder)

    if not os.path.exists(outputLdifFolder):
        os.mkdir(outputLdifFolder)

    # rewrite service location as CentOS and Ubuntu have different values
    service = getOutput(['whereis', 'service']).split(' ')[1].strip()

    stopTomcat()
    preparePasswordFile()
    stopOpenDJ()
    copyFiles(backup24_folder)
    updateCertKeystore()
    copyCustomLDAPSchema(backup24_folder)

    exportLDIF(outputFolder)
    processPeople(ldif_folder)
    processLDIF(ldif_folder, outputFolder)
    importLDIF(outputFolder)

    startOpenDJ()
    setPermissions()
    startTomcat()

    # remove the password_file
    os.remove(password_file)
    logging.info("Import finished. Check output_ldif/processed.ldif.rejects" +
                 " for DN's that were rejected during import.")


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print "Usage: ./import244.py <path_to_backup_folder>"
        print "Example:\n ./import244.py /root/backup_24"
    else:
        main(sys.argv[1])
