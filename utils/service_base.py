import logging

logger = logging.getLogger(__name__)


class ServiceBase(object):
    manager = None

    def all(self, *args, **kwargs):
        try:
            return self.manager.all(*args, **kwargs)
        except Exception as e:
            logger.exception('%s Service all exception: %s' % (self.manager.model.__name__, e))
            return None

    def get(self, *args, **kwargs):
        try:
            return self.manager.get(*args, **kwargs)
        except Exception as e:
            logger.exception('%s Service get exception: %s' % (self.manager.model.__name__, e))
            return None

    def filter(self, *args, **kwargs):
        try:
            return self.manager.filter(*args, **kwargs)
        except Exception as e:
            logger.exception('%s Service filter exception: %s' % (self.manager.model.__name__, e))
            return None

    def create(self, *args, **kwargs):
        try:
            return self.manager.create(**kwargs)
        except Exception as e:
            logger.exception('%s Service create exception: %s' % (self.manager.model.__name__, e))
            return None

    def update(self, pk, *args, **kwargs):
        try:
            record = self.get(id=pk)
            if record is not None:
                for k, v in kwargs.items():
                    setattr(record, k, v)
                record.save()
                record.refresh_from_db()
                return record
        except Exception as e:
            logger.exception('%s Service update exception: %s' % (self.manager.model.__name__, e))
            return None

    def get_or_create(self, *args, **kwargs):
        try:
            instance, created = self.manager.get_or_create(**kwargs)
            return instance
        except Exception as e:
            logger.exception('%s Service get_or_create exception: %s' %(self.manager.model.__name__, e))
            return None

