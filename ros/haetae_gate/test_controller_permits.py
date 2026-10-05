import copy
from pathlib import Path
from types import SimpleNamespace as S
import tempfile
import unittest
from unittest import mock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
from controller_permits import (DOMAIN, PermitSigner, IDLE, base_digest, arm_digest, explicit_rearm)


def stamp(value=1_000_000_000):
    return S(sec=value//1_000_000_000, nanosec=value%1_000_000_000)


def base():
    return S(header=S(stamp=stamp()), twist=S(linear=S(x=.15,y=0.,z=0.), angular=S(x=0.,y=0.,z=.2)))


def arm():
    return S(header=S(stamp=stamp()), joint_names=['joint1','joint2','joint3','joint4'], points=[
        S(positions=[0.,-1.,.7,.3], time_from_start=stamp(0), velocities=[], accelerations=[], effort=[]),
        S(positions=[.3,-1.,.7,.3], time_from_start=stamp(), velocities=[], accelerations=[], effort=[])])


class ControllerPermitTest(unittest.TestCase):
    def test_base_payload_binds_all_actuating_fields_and_clock(self):
        msg=base(); original=base_digest(msg)
        self.assertEqual(original,'258d4f3f62b83e2c144be7d2fc926faf92000525bdc1a305f9b674eea4c93fdb')
        for component in ('linear','angular'):
            for axis in ('x','y','z'):
                changed=copy.deepcopy(msg)
                value=getattr(getattr(changed.twist,component),axis)
                setattr(getattr(changed.twist,component),axis,value+.1)
                if (component,axis) in (('linear','x'),('angular','z')):
                    self.assertNotEqual(base_digest(changed),original)
                else:
                    with self.assertRaises(ValueError):base_digest(changed)
        msg.header.stamp.nanosec=1
        self.assertNotEqual(base_digest(msg),original)
        msg.twist.linear.x=float('nan')
        with self.assertRaises(ValueError):base_digest(msg)

    def test_arm_payload_binds_positions_times_names_and_rejects_extensions(self):
        trajectory=arm(); original=arm_digest(trajectory)
        self.assertEqual(original,'0b836ee3a5aae545c294e357ce857c032f686bebc673294f90be526c41a04e83')
        for index in range(4):
            changed=copy.deepcopy(trajectory); changed.points[1].positions[index]+=.01
            self.assertNotEqual(arm_digest(changed),original)
        changed=copy.deepcopy(trajectory); changed.points[1].time_from_start.nanosec=1
        with self.assertRaises(ValueError):arm_digest(changed)  # >1 second
        changed=copy.deepcopy(trajectory); changed.points[1].time_from_start.sec=0; changed.points[1].time_from_start.nanosec=999000000
        self.assertNotEqual(arm_digest(changed),original)
        changed=copy.deepcopy(trajectory); changed.points[1].time_from_start=stamp(0)
        with self.assertRaises(ValueError):arm_digest(changed)
        for field in ('velocities','accelerations','effort'):
            changed=copy.deepcopy(trajectory); setattr(changed.points[0],field,[0.]*4)
            with self.assertRaises(ValueError):arm_digest(changed)
        trajectory.joint_names.reverse()
        with self.assertRaises(ValueError):arm_digest(trajectory)

    def test_signed_envelope_binds_target_nonce_sequence_and_original_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            key=Ed25519PrivateKey.generate(); path=Path(tmp)/'private.key'
            path.write_text(key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()).hex())
            signer=PermitSigner(path)
            with self.assertRaises(ValueError):signer.sign('base','command',IDLE,1)
            signer.observe('base',{'nonce':'a'*32})
            with mock.patch('controller_permits.time.monotonic_ns',return_value=2000):
                token=signer.sign('base','command',base_digest(base()),1000,123)
            body,signature=token.rsplit(':',1)
            key.public_key().verify(bytes.fromhex(signature),DOMAIN+body.encode())
            fields=body.split(':')
            self.assertEqual(fields[:8],['v1','base-command','a'*32,'1','1000','2000','1123','2123'])
            with self.assertRaises(Exception):
                key.public_key().verify(bytes.fromhex(signature),DOMAIN+body.replace('base-command','arm-goal').encode())
            with self.assertRaises(ValueError):signer.sign('base','command',IDLE,1,0)
            with self.assertRaises(ValueError):signer.observe('arm',{'nonce':'Z'*32})

    def test_only_explicit_accepted_stop_can_issue_reset(self):
        step={'outcome':{'decision':{'action':{'type':'stop'},'verdict':'yun'}},
              'status':{'armed':['vla'],'active':None,'mode':'normal','arm_cancelling':False}}
        self.assertTrue(explicit_rearm(step))
        for status in ({'armed':[]},{'active':{'id':1}},{'mode':'hold'},{'arm_cancelling':True}):
            changed=copy.deepcopy(step); changed['status'].update(status)
            self.assertFalse(explicit_rearm(changed))
        for outcome in ({'world_updated':{}},{'rejected':{'error':'replay'}}):
            changed=copy.deepcopy(step); changed['outcome']=outcome
            self.assertFalse(explicit_rearm(changed))
