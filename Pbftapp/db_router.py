
from django.conf import settings

class PBFTDatabaseRouter:
    def db_for_read(self, model, **hints):
        """
        Attempts to read from the appropriate database based on the model.
        """
        return 'default'

    def db_for_write(self, model, **hints):
        """
        Attempts to write to the appropriate database based on the model.
        """
        return 'default'

    def allow_relation(self, obj1, obj2, **hints):
        """
        Allow relations if both objects are in the same database.
        """
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        Make sure the app only appears in the 'default' database.
        """
        return True
