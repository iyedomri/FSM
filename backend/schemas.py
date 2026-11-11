from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

ROOMS = {"A101", "A102", "A201", "A202", "LAB1", "LAB2", "LIB", "AMPHI"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Login(StrictModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize(cls, value):
        return value.casefold()


class Register(Login):
    invite_code: str = Field(min_length=12, max_length=128)
    name: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("name")
    @classmethod
    def trim(cls, value):
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Name is too short")
        return value


class ScheduleInput(StrictModel):
    course: str = Field(min_length=2, max_length=100)
    room: str
    weekday: int = Field(ge=0, le=6)
    start_minute: int = Field(ge=0, lt=1440)
    end_minute: int = Field(gt=0, le=1440)

    @model_validator(mode="after")
    def validate_slot(self):
        if self.room not in ROOMS or self.end_minute <= self.start_minute:
            raise ValueError("Choose a valid schematic room and time interval")
        return self


class Attempt(StrictModel):
    answers: list[int] = Field(min_length=5, max_length=5)

    @field_validator("answers")
    @classmethod
    def valid_answers(cls, value):
        if any(type(i) is not int or not 0 <= i < 4 for i in value):
            raise ValueError("Answers must be indexes from 0 to 3")
        return value


class Ask(StrictModel):
    question: str = Field(min_length=3, max_length=2000)
