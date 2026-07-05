"""Create an idempotent demo scenario for user testing."""

import os
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Model
from django.utils import timezone

from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    Equipment,
    OwnerProfile,
    Specialty,
    TenantDoctorProfile,
    TenantDoctorStatus,
)
from apps.finance.models import Payment, PaymentMethod, PriceType, RateRule, Settlement
from apps.finance.services.payment_service import register_payment, validate_payment
from apps.finance.services.settlement_service import (
    generate_settlement_for_reservation,
    mark_settlement_as_paid,
)
from apps.identity.models import UserRole
from apps.integration.models import AccessCredential
from apps.integration.services.access_simulator import provision_access_for_reservation
from apps.scheduling.models import (
    AvailabilityRule,
    Reservation,
    ReservationStatus,
    Weekday,
)
from apps.scheduling.services.reservation_service import (
    confirm_reservation,
    create_reservation,
)
from apps.vault.models import DocumentAsset, DocumentStatus, DocumentType
from apps.vault.services.document_service import (
    approve_document,
    mark_document_in_review,
    upload_document,
)

DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "DemoPass123!")
DEMO_MARKER = "[demo-user-testing]"


class Command(BaseCommand):
    help = "Crea datos demo idempotentes para pruebas de usuario del MVP."

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        admin = self._user(
            "demo.admin@example.com",
            "Admin",
            "Demo",
            UserRole.ADMIN,
            is_staff=True,
        )
        owner_one_user = self._user(
            "demo.owner.norte@example.com",
            "Andrea",
            "Propietaria",
            UserRole.OWNER,
        )
        owner_two_user = self._user(
            "demo.owner.sur@example.com",
            "Roberto",
            "Propietario",
            UserRole.OWNER,
        )
        self._user(
            "demo.operator@example.com", "Olivia", "Operadora", UserRole.OPERATOR
        )
        self._user(
            "demo.reception@example.com",
            "Renata",
            "Recepcionista",
            UserRole.RECEPTIONIST,
        )
        self._user("demo.auditor@example.com", "Aurelio", "Auditor", UserRole.AUDITOR)

        clinics = self._clinics(admin)
        specialties = self._specialties()
        equipment = self._equipment()
        owners = (
            self._owner_profile(owner_one_user, "Dra. Andrea Propietaria"),
            self._owner_profile(owner_two_user, "Dr. Roberto Propietario"),
        )
        doctors = self._tenant_doctors(specialties)
        rooms = self._rooms(clinics, owners, specialties, equipment, admin)

        for room in rooms:
            self._availability(room, admin)
            self._rates(room, admin)

        reservations = self._reservations(rooms, doctors, admin)
        self._documents(owners, doctors, rooms, reservations, admin)

        self.stdout.write(
            self.style.SUCCESS(
                "Datos demo listos. Usuario demo: demo.admin@example.com "
                f"con contraseña {DEMO_PASSWORD}"
            )
        )

    def _user(
        self,
        email: str,
        first_name: str,
        last_name: str,
        role: str,
        *,
        is_staff: bool = False,
    ) -> Any:
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            email=email,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "role": role,
                "is_staff": is_staff,
                "is_active": True,
            },
        )
        updates = []
        for field_name, value in (
            ("first_name", first_name),
            ("last_name", last_name),
            ("role", role),
            ("is_staff", is_staff),
            ("is_active", True),
        ):
            if getattr(user, field_name) != value:
                setattr(user, field_name, value)
                updates.append(field_name)
        if created:
            user.set_password(DEMO_PASSWORD)
            updates.append("password")
        if updates:
            user.save(update_fields=updates)
        return user

    def _clinics(self, admin: Any) -> tuple[Clinic, Clinic]:
        north, _ = Clinic.objects.get_or_create(
            name="Demo Clínica Norte",
            defaults={
                "address": "Av. Salud 100, Monterrey",
                "phone": "8180001000",
                "email": "norte.demo@example.com",
                "schedule_text": "Lunes a sábado 08:00-20:00",
                "created_by": admin,
                "updated_by": admin,
            },
        )
        south, _ = Clinic.objects.get_or_create(
            name="Demo Clínica Sur",
            defaults={
                "address": "Blvd. Consulta 250, San Pedro Garza García",
                "phone": "8180002000",
                "email": "sur.demo@example.com",
                "schedule_text": "Lunes a sábado 08:00-18:00",
                "created_by": admin,
                "updated_by": admin,
            },
        )
        return north, south

    def _specialties(self) -> tuple[Specialty, Specialty, Specialty]:
        values = (
            ("Cardiología", "Atención cardiovascular."),
            ("Dermatología", "Piel y procedimientos ambulatorios."),
            ("Nutrición", "Consulta nutricional clínica."),
        )
        specialties = tuple(
            Specialty.objects.get_or_create(
                name=name,
                defaults={"description": description},
            )[0]
            for name, description in values
        )
        return specialties[0], specialties[1], specialties[2]

    def _equipment(self) -> tuple[Equipment, Equipment, Equipment]:
        values = (
            ("Camilla eléctrica", "Camilla ajustable para consulta."),
            ("Ultrasonido básico", "Equipo diagnóstico de apoyo."),
            ("Monitor de signos vitales", "Monitoreo clínico básico."),
        )
        equipment = tuple(
            Equipment.objects.get_or_create(
                name=name,
                defaults={"description": description},
            )[0]
            for name, description in values
        )
        return equipment[0], equipment[1], equipment[2]

    def _owner_profile(self, user: Any, display_name: str) -> OwnerProfile:
        owner, _ = OwnerProfile.objects.get_or_create(
            user=user,
            defaults={
                "display_name": display_name,
                "professional_license": "DUE-DEMO",
                "tax_id": "DEMO010101AAA",
                "phone": "8111111111",
                "notes": "Propietario demo para pruebas de usuario.",
            },
        )
        owner.display_name = display_name
        owner.is_active = True
        owner.save(update_fields=["display_name", "is_active", "updated_at"])
        return owner

    def _tenant_doctors(
        self,
        specialties: tuple[Specialty, Specialty, Specialty],
    ) -> tuple[TenantDoctorProfile, ...]:
        doctor_specs = (
            (
                "demo.doctor.cardiologia@example.com",
                "Carla",
                "Cardióloga",
                "Dra. Carla Cardióloga",
                TenantDoctorStatus.AUTHORIZED,
                True,
                (specialties[0],),
            ),
            (
                "demo.doctor.derma@example.com",
                "Diego",
                "Dermatólogo",
                "Dr. Diego Dermatólogo",
                TenantDoctorStatus.AUTHORIZED,
                True,
                (specialties[1],),
            ),
            (
                "demo.doctor.nutricion@example.com",
                "Natalia",
                "Nutrióloga",
                "Dra. Natalia Nutrióloga",
                TenantDoctorStatus.AUTHORIZED,
                True,
                (specialties[2],),
            ),
            (
                "demo.doctor.inactivo@example.com",
                "Iván",
                "Inactivo",
                "Dr. Iván Inactivo",
                TenantDoctorStatus.SUSPENDED,
                False,
                (specialties[0], specialties[2]),
            ),
        )
        doctors = []
        for (
            email,
            first_name,
            last_name,
            display_name,
            status,
            active,
            specs,
        ) in doctor_specs:
            user = self._user(email, first_name, last_name, UserRole.TENANT_DOCTOR)
            profile, _ = TenantDoctorProfile.objects.get_or_create(
                user=user,
                defaults={
                    "display_name": display_name,
                    "professional_license": f"CED-{email.split('@')[0][-6:].upper()}",
                    "tax_id": "MEDD010101AAA",
                    "phone": "8122222222",
                    "status": status,
                    "is_active": active,
                    "notes": "Médico demo para pruebas de usuario.",
                },
            )
            profile.display_name = display_name
            profile.status = status
            profile.is_active = active
            profile.save(
                update_fields=["display_name", "status", "is_active", "updated_at"]
            )
            profile.specialties.set(specs)
            doctors.append(profile)
        return tuple(doctors)

    def _rooms(
        self,
        clinics: tuple[Clinic, Clinic],
        owners: tuple[OwnerProfile, OwnerProfile],
        specialties: tuple[Specialty, Specialty, Specialty],
        equipment: tuple[Equipment, Equipment, Equipment],
        admin: Any,
    ) -> tuple[ConsultingRoom, ...]:
        room_specs = (
            (clinics[0], owners[0], "Consultorio Norte 101", "1", specialties[:2]),
            (clinics[0], owners[0], "Consultorio Norte 102", "1", specialties[1:]),
            (clinics[1], owners[1], "Consultorio Sur 201", "2", specialties[:1]),
            (clinics[1], owners[1], "Consultorio Sur 202", "2", specialties),
        )
        rooms = []
        for clinic, owner, name, floor, allowed in room_specs:
            room, _ = ConsultingRoom.objects.get_or_create(
                clinic=clinic,
                name=name,
                defaults={
                    "owner": owner,
                    "floor": floor,
                    "capacity": 2,
                    "description": f"{name} preparado para pruebas de agenda.",
                    "regulations_text": "Uso demo: limpieza al cierre de bloque.",
                    "created_by": admin,
                    "updated_by": admin,
                },
            )
            room.owner = owner
            room.is_active = True
            room.save(update_fields=["owner", "is_active", "updated_at"])
            room.allowed_specialties.set(allowed)
            room.excluded_specialties.clear()
            room.equipment.set(equipment[:2])
            rooms.append(room)
        return tuple(rooms)

    def _availability(self, room: ConsultingRoom, admin: Any) -> None:
        for weekday in (
            Weekday.MONDAY,
            Weekday.TUESDAY,
            Weekday.WEDNESDAY,
            Weekday.THURSDAY,
            Weekday.FRIDAY,
        ):
            for label, start_time, end_time in (
                ("matutina", time(8, 0), time(13, 0)),
                ("vespertina", time(13, 0), time(18, 0)),
            ):
                if AvailabilityRule.objects.filter(
                    room=room,
                    weekday=weekday,
                    start_time=start_time,
                    end_time=end_time,
                    is_deleted=False,
                ).exists():
                    continue
                AvailabilityRule.objects.create(
                    room=room,
                    name=f"{room.name} {label} {weekday.label}",
                    weekday=weekday,
                    start_time=start_time,
                    end_time=end_time,
                    start_date=date(2026, 1, 1),
                    notes="Regla demo para pruebas de usuario.",
                    created_by=admin,
                    updated_by=admin,
                )

    def _rates(self, room: ConsultingRoom, admin: Any) -> None:
        rate_specs = (
            (
                "Tarifa demo mañana",
                [0, 1, 2, 3, 4],
                time(8, 0),
                time(13, 0),
                "75.00",
                1,
            ),
            (
                "Tarifa demo tarde",
                [0, 1, 2, 3, 4],
                time(13, 0),
                time(18, 0),
                "90.00",
                2,
            ),
            ("Tarifa demo sábado", [5], time(9, 0), time(14, 0), "120.00", 1),
        )
        for name, weekdays, start_time, end_time, amount, priority in rate_specs:
            if RateRule.objects.filter(room=room, name=name, is_deleted=False).exists():
                continue
            RateRule.objects.create(
                room=room,
                name=name,
                weekdays=weekdays,
                start_time=start_time,
                end_time=end_time,
                start_date=date(2026, 1, 1),
                price_type=PriceType.HOURLY,
                amount=Decimal(amount),
                currency="MXN",
                priority=priority,
                notes="Tarifa demo para pruebas de usuario.",
                created_by=admin,
                updated_by=admin,
            )

    def _reservations(
        self,
        rooms: tuple[ConsultingRoom, ...],
        doctors: tuple[TenantDoctorProfile, ...],
        admin: Any,
    ) -> tuple[Reservation, ...]:
        monday = _next_monday()
        specs = (
            ("solicitada", rooms[0], doctors[0], monday, time(8, 0), time(13, 0)),
            (
                "pendiente-pago",
                rooms[1],
                doctors[1],
                monday + timedelta(days=1),
                time(8, 0),
                time(13, 0),
            ),
            (
                "pagada",
                rooms[2],
                doctors[2],
                monday + timedelta(days=2),
                time(13, 0),
                time(18, 0),
            ),
            (
                "confirmada",
                rooms[3],
                doctors[0],
                monday + timedelta(days=3),
                time(8, 0),
                time(13, 0),
            ),
        )
        reservations = []
        for slug, room, doctor, reservation_date, start_time, end_time in specs:
            reservation = self._reservation(
                slug,
                room,
                doctor,
                reservation_date,
                start_time,
                end_time,
                admin,
            )
            reservations.append(reservation)

        pending = reservations[1]
        if pending.status == ReservationStatus.REQUESTED:
            pending.status = ReservationStatus.PENDING_PAYMENT
            pending.updated_by = admin
            pending.save(update_fields=["status", "updated_by", "updated_at"])

        paid = self._fully_paid(reservations[2], admin, "DEMO-PAGO-VALIDADO")
        confirmed = self._fully_paid(reservations[3], admin, "DEMO-PAGO-CONFIRMADO")
        if confirmed.status != ReservationStatus.CONFIRMED:
            confirmed = confirm_reservation(reservation=confirmed, actor=admin)
        if not AccessCredential.objects.filter(reservation=confirmed).exists():
            provision_access_for_reservation(confirmed, user=admin)

        self._settlements(paid, confirmed, admin)
        return tuple(reservations)

    def _reservation(
        self,
        slug: str,
        room: ConsultingRoom,
        doctor: TenantDoctorProfile,
        reservation_date: date,
        start_time: time,
        end_time: time,
        admin: Any,
    ) -> Reservation:
        marker = f"{DEMO_MARKER} {slug}"
        existing = Reservation.objects.filter(notes__icontains=marker).first()
        if existing:
            return existing
        return create_reservation(
            room=room,
            tenant_doctor=doctor,
            reservation_date=reservation_date,
            start_time=start_time,
            end_time=end_time,
            notes=f"{marker} Reservación demo.",
            actor=admin,
        )

    def _fully_paid(
        self,
        reservation: Reservation,
        admin: Any,
        reference: str,
    ) -> Reservation:
        statement = reservation.statements.order_by("-version").first()
        if statement is None:
            return reservation
        payment = Payment.objects.filter(
            reservation=reservation,
            reference=reference,
            is_deleted=False,
        ).first()
        if payment is None:
            payment = register_payment(
                reservation=reservation,
                amount=statement.total_doctor,
                currency=statement.currency,
                method=PaymentMethod.TRANSFER,
                reference=reference,
                payment_date=timezone.localdate(),
                notes="Pago demo validado.",
                actor=admin,
            )
        if payment.status != "validado":
            validate_payment(payment=payment, actor=admin)
        reservation.refresh_from_db()
        return reservation

    def _settlements(
        self,
        paid: Reservation,
        confirmed: Reservation,
        admin: Any,
    ) -> None:
        paid_settlement = Settlement.objects.filter(
            reservation=paid,
            is_deleted=False,
        ).first()
        if paid_settlement is None:
            generate_settlement_for_reservation(
                reservation=paid,
                notes="Liquidación demo calculada.",
                actor=admin,
            )

        confirmed_settlement = Settlement.objects.filter(
            reservation=confirmed,
            is_deleted=False,
        ).first()
        if confirmed_settlement is None:
            confirmed_settlement = generate_settlement_for_reservation(
                reservation=confirmed,
                notes="Liquidación demo pagada.",
                actor=admin,
            )
        if confirmed_settlement.status != "pagada":
            mark_settlement_as_paid(
                settlement=confirmed_settlement,
                reference="DEMO-LIQ-PAGADA",
                payment_date=timezone.localdate(),
                notes="Liquidación demo marcada como pagada.",
                actor=admin,
            )

    def _documents(
        self,
        owners: tuple[OwnerProfile, OwnerProfile],
        doctors: tuple[TenantDoctorProfile, ...],
        rooms: tuple[ConsultingRoom, ...],
        reservations: tuple[Reservation, ...],
        admin: Any,
    ) -> None:
        self._document(
            "Demo contrato propietario",
            DocumentType.CONTRACT,
            admin,
            status=DocumentStatus.APPROVED,
            owner=owners[0],
        )
        self._document(
            "Demo cédula médico",
            DocumentType.PROFESSIONAL_LICENSE,
            admin,
            status=DocumentStatus.RECEIVED,
            tenant_doctor=doctors[0],
        )
        self._document(
            "Demo reglamento consultorio",
            DocumentType.REGULATIONS,
            admin,
            status=DocumentStatus.IN_REVIEW,
            room=rooms[0],
        )
        self._document(
            "Demo comprobante reservación",
            DocumentType.PAYMENT_RECEIPT,
            admin,
            status=DocumentStatus.APPROVED,
            reservation=reservations[3],
        )

    def _document(
        self,
        title: str,
        document_type: str,
        admin: Any,
        *,
        status: str,
        **entity: Model,
    ) -> DocumentAsset:
        existing = DocumentAsset.objects.filter(title=title, is_deleted=False).first()
        if existing is not None:
            return existing

        file = ContentFile(
            f"{title}\n{DEMO_MARKER}\n".encode(),
            name=f"{title.lower().replace(' ', '_')}.txt",
        )
        document = upload_document(
            title=title,
            document_type=document_type,
            file=file,
            notes="Documento demo para pruebas de usuario.",
            actor=admin,
            **entity,
        )
        if status == DocumentStatus.IN_REVIEW:
            return mark_document_in_review(document=document, actor=admin)
        if status == DocumentStatus.APPROVED:
            document = mark_document_in_review(document=document, actor=admin)
            return approve_document(document=document, actor=admin)
        return document


def _next_monday() -> date:
    today = timezone.localdate()
    days_until_monday = (int(Weekday.MONDAY) - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    return today + timedelta(days=days_until_monday)
