




class RideRepository:
    def __init__(self, db: AsyncSession):
        self.db = db
