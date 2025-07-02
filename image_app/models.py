import hashlib
import json
import time
from django.db import models
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder

class Block(models.Model):
    """Represents a block in the blockchain"""
    index = models.PositiveIntegerField(unique=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    previous_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)
    nonce = models.PositiveIntegerField(default=0)
    data = models.JSONField(encoder=DjangoJSONEncoder)
    
    def __str__(self):
        return f'Block {self.index} - {self.hash[:10]}...'
    
    def calculate_hash(self):
        """Calculate the hash of the block"""
        block_string = json.dumps({
            'index': self.index,
            'timestamp': str(self.timestamp),
            'previous_hash': self.previous_hash,
            'data': self.data,
            'nonce': self.nonce
        }, sort_keys=True).encode()
        return hashlib.sha256(block_string).hexdigest()
    
    def mine_block(self, difficulty=4):
        """Mine the block with the given difficulty"""
        prefix = '0' * difficulty
        while not self.hash.startswith(prefix):
            self.nonce += 1
            self.hash = self.calculate_hash()


class Transaction(models.Model):
    """Represents a transaction in the blockchain"""
    TRANSACTION_TYPES = [
        ('SEARCH', 'Image Search'),
        ('VIEW', 'Image View'),
        ('UPLOAD', 'Image Upload'),
    ]
    
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    data = models.JSONField(encoder=DjangoJSONEncoder)
    timestamp = models.DateTimeField(auto_now_add=True)
    block = models.ForeignKey(Block, on_delete=models.CASCADE, related_name='transactions', null=True, blank=True)
    signature = models.CharField(max_length=256, blank=True)
    
    def __str__(self):
        return f'{self.transaction_type} - {self.timestamp}'
    
    def sign(self, private_key):
        """Sign the transaction"""
        # In a real implementation, you would use a proper signing mechanism
        # This is a simplified version for demonstration
        message = f"{self.transaction_type}{json.dumps(self.data, sort_keys=True)}{self.timestamp}"
        self.signature = hashlib.sha256(message.encode()).hexdigest()


class Validator(models.Model):
    """Represents a node in the PBFT network"""
    name = models.CharField(max_length=100)
    address = models.GenericIPAddressField()
    port = models.PositiveIntegerField()
    public_key = models.TextField(help_text="PEM formatted public key")
    is_active = models.BooleanField(default=True)
    
    def __str__(self):
        return f'{self.name} ({self.address}:{self.port})'
    
    @property
    def node_url(self):
        """Get the full node URL"""
        return f'http://{self.address}:{self.port}'


class BlockchainState(models.Model):
    """Stores the current state of the blockchain"""
    last_block = models.OneToOneField(Block, on_delete=models.CASCADE, related_name='chain_state', null=True, blank=True)
    difficulty = models.PositiveIntegerField(default=4)
    active_nodes = models.PositiveIntegerField(default=1)
    last_block_number = models.PositiveIntegerField(default=0)
    total_transactions = models.PositiveIntegerField(default=0)
    
    @classmethod
    def get_instance(cls):
        """Get or create the singleton instance"""
        instance, created = cls.objects.get_or_create(pk=1)
        return instance
    
    def update_state(self, block=None, transaction_count=0):
        """Update the blockchain state"""
        if block:
            self.last_block = block
            self.last_block_number = block.index
        self.total_transactions += transaction_count
        self.save()
    
    def __str__(self):
        if self.last_block:
            return f'Blockchain State - Last Block: {self.last_block.index}, Active Nodes: {self.active_nodes}, Total TXs: {self.total_transactions}'
        return 'Blockchain State - Genesis'
