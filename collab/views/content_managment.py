import json
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib.gis.geos import GEOSGeometry
from django.contrib.gis.geos.error import GEOSException
from django.db import IntegrityError, transaction
from django.db.models import F
from django.forms import modelformset_factory
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic.detail import DetailView
from django.views.generic.detail import SingleObjectMixin
from django.views.generic.edit import CreateView
from django.views.generic.edit import DeleteView
from django.views.decorators.csrf import csrf_exempt
from api.serializers import EventSerializer
from api.serializers import FeatureTypeSerializer
from api.serializers import ProjectDetailedSerializer

from collab.exif import exif
from collab.forms import AuthorizationForm
from collab.forms import CustomFieldModelForm
from collab.forms import CustomFieldModelBaseFS
from collab.forms import AttachmentForm
from collab.forms import CommentForm
from collab.forms import ExtendedBaseFS
from collab.forms import FeatureTypeModelForm
from collab.forms import FeatureBaseForm
from collab.forms import FeatureExtraForm
from collab.forms import FeatureLinkForm
from collab.forms import ProjectModelForm
from collab.models import Authorization
from collab.models import Attachment
from collab.models import Comment
from collab.models import CustomField
from collab.models import Event
from collab.models import Feature
from collab.models import FeatureType
from collab.models import FeatureLink
from collab.models import Project
from collab.models import Subscription

import logging
logger = logging.getLogger('django')

DECORATORS = [csrf_exempt, login_required(login_url=settings.LOGIN_URL)]

User = get_user_model()

####################
# ATTACHMENT VIEWS #
####################


@method_decorator(DECORATORS, name='dispatch')
class AttachmentCreate(SingleObjectMixin, UserPassesTestMixin, View):
    queryset = Feature.objects.all()
    pk_url_kwarg = 'feature_id'

    def test_func(self):
        user = self.request.user
        feature = self.get_object()
        project = feature.project
        return Authorization.has_permission(user, 'can_update_feature', project, feature)

    def post(self, request, slug, feature_type_slug, feature_id):
        feature = self.get_object()
        project = feature.project
        user = request.user
        form = AttachmentForm(request.POST or None, request.FILES)
        if form.is_valid():
            try:
                Attachment.objects.create(
                    feature_id=feature.feature_id,
                    author=user,
                    project=project,
                    type_objet='feature',
                    attachment_file=form.cleaned_data.get('attachment_file')
                )
            except Exception as err:
                messages.error(
                    request,
                    "Erreur à l'ajout de la pièce jointe: {err}".format(err=str(err)))
            else:
                messages.info(request, "Ajout de la pièce jointe confirmé")
        else:
            messages.error(request, "Erreur à l'ajout de la pièce jointe")

        return redirect(
            'collab:feature_update', slug=slug,
            feature_type_slug=feature_type_slug, feature_id=feature_id)


#################
# COMMENT VIEWS #
#################


@method_decorator(DECORATORS, name='dispatch')
class CommentCreate(SingleObjectMixin, UserPassesTestMixin, View):
    queryset = Feature.objects.all()
    pk_url_kwarg = 'feature_id'

    def test_func(self):
        user = self.request.user
        feature = self.get_object()
        project = feature.project
        return Authorization.has_permission(user, 'can_view_feature', project, feature)

    def post(self, request, slug, feature_type_slug, feature_id):
        feature = self.get_object()
        project = feature.project
        user = request.user
        form = CommentForm(request.POST, request.FILES)

        linked_features = FeatureLink.objects.filter(
            feature_from=feature.feature_id
        )

        if form.is_valid():
            try:
                comment = Comment.objects.create(
                    feature_id=feature.feature_id,
                    feature_type_slug=feature.feature_type.slug,
                    author=user,
                    project=project,
                    comment=form.cleaned_data.get('comment')
                )
                up_file = form.cleaned_data.get('attachment_file')
                title = form.cleaned_data.get('title')
                info = form.cleaned_data.get('info')
                if comment and up_file and title:
                    Attachment.objects.create(
                        feature_id=feature.feature_id,
                        author=user,
                        project=project,
                        comment=comment,
                        attachment_file=up_file,
                        title=title,
                        info=info,
                        type_objet='comment'
                    )

            except Exception as err:
                logger.exception('CommentCreate.post')
                messages.error(
                    request,
                    "Erreur à l'ajout du commentaire: {err}".format(err=err))
            else:
                messages.info(request, "Ajout du commentaire confirmé")

            return redirect(
                'collab:feature_detail',
                slug=slug,
                feature_type_slug=feature_type_slug,
                feature_id=feature_id)
        else:
            logger.error(form.errors)

        context = {
            'feature': feature,
            'feature_data': feature.custom_fields_as_list,
            'feature_types': FeatureType.objects.filter(project=project),
            'feature_type': feature.feature_type,
            'linked_features': linked_features,
            'project': project,
            'permissions': Authorization.all_permissions(user, project),
            'comments': Comment.objects.filter(project=project, feature_id=feature.feature_id),
            'attachments': Attachment.objects.filter(project=project, feature_id=feature.feature_id),
            'comment_form': form,
        }
        return render(request, 'collab/feature/feature_detail.html', context)


#################
# FEATURE VIEWS #
#################


@method_decorator(DECORATORS, name='dispatch')
class FeatureCreate(SingleObjectMixin, UserPassesTestMixin, View):
    queryset = FeatureType.objects.all()
    slug_url_kwarg = 'feature_type_slug'

    def test_func(self):
        user = self.request.user
        feature_type = self.get_object()
        return Authorization.has_permission(user, 'can_create_feature', feature_type.project)

    def get(self, request, slug, feature_type_slug):
        user = request.user
        feature_type = self.get_object()
        project = feature_type.project
        extra = CustomField.objects.filter(feature_type=feature_type)

        feature_form = FeatureBaseForm(feature_type=feature_type, user=user)
        extra_form = FeatureExtraForm(extra=extra)

        context = {
            'project': project,
            'feature_type': feature_type,
            'feature_types': project.featuretype_set.all(),
            'permissions': Authorization.all_permissions(user, project),
            'feature_form': feature_form,
            'extra_form': extra_form,
            'action': 'create'
        }

        return render(request, 'collab/feature/feature_edit.html', context)

    def post(self, request, slug, feature_type_slug):

        user = request.user
        feature_type = self.get_object()
        project = feature_type.project

        feature_form = FeatureBaseForm(
            request.POST, feature_type=feature_type, user=user)
        extra = CustomField.objects.filter(feature_type=feature_type)
        extra_form = FeatureExtraForm(request.POST, extra=extra)

        if feature_form.is_valid() and extra_form.is_valid():
            try:
                feature = feature_form.save(
                    project=project,
                    feature_type=feature_type,
                    creator=user,
                    extra=extra_form.cleaned_data
                )
            except Exception as err:
                logger.exception('FeatureCreate.post')
                messages.error(
                    request,
                    "Une erreur s'est produite lors de la création du signalement {title}: {err}".format(
                        title=feature_form.cleaned_data.get('title', 'N/A'),
                        err=str(err)))
            else:
                messages.info(
                    request,
                    "Le signalement {title} a bien été crée. ".format(
                        title=feature_form.cleaned_data.get('title', 'N/A'),
                    ))
                return redirect(
                    'collab:feature_detail', slug=project.slug,
                    feature_type_slug=feature_type.slug, feature_id=feature.feature_id)

        else:
            logger.error(feature_form.errors)
            logger.error(extra_form.errors)

        import pdb; pdb.set_trace()

        context = {
            'project': project,
            'feature_type': feature_type,
            'feature_types': project.featuretype_set.all(),
            'permissions': Authorization.all_permissions(user, project),
            'feature_form': feature_form,
            'extra_form': extra_form,
            'action': 'create'
        }
        return render(request, 'collab/feature/feature_edit.html', context)


@method_decorator(DECORATORS, name='dispatch')
class FeatureList(SingleObjectMixin, UserPassesTestMixin, View):
    queryset = Project.objects.all()

    def test_func(self):
        user = self.request.user
        project = self.get_object()
        return Authorization.has_permission(user, 'can_view_feature', project)

    def get(self, request, slug):
        project = self.get_object()
        user = request.user
        permissions = Authorization.all_permissions(user, project)
        feature_types = FeatureType.objects.filter(project=project)
        context = {
            'features': Feature.handy.availables(user, project).order_by('-status', 'created_on'),
            'feature_types': feature_types,
            'project': project,
            'permissions': permissions,
        }

        return render(request, 'collab/feature/feature_list.html', context)


@method_decorator(DECORATORS, name='dispatch')
class FeatureDetail(SingleObjectMixin, UserPassesTestMixin, View):

    queryset = Feature.objects.all()
    pk_url_kwarg = 'feature_id'

    def test_func(self):
        user = self.request.user
        feature = self.get_object()
        project = feature.project
        return Authorization.has_permission(user, 'can_view_feature', project, feature)

    def get(self, request, slug, feature_type_slug, feature_id):
        user = request.user
        feature = self.get_object()
        project = feature.project

        linked_features = FeatureLink.objects.filter(
            feature_from=feature.feature_id
        )
        events = Event.objects.filter(feature_id=feature.feature_id).order_by('-created_on')
        serialized_events = EventSerializer(events, many=True)

        context = {
            'feature': feature,
            'feature_data': feature.custom_fields_as_list,
            'feature_types': FeatureType.objects.filter(project=project),
            'feature_type': feature.feature_type,
            'linked_features': linked_features,
            'project': project,
            'permissions': Authorization.all_permissions(user, project, feature),
            'comments': Comment.objects.filter(project=project, feature_id=feature.feature_id),
            'events': serialized_events.data,
            'comment_form': CommentForm(),
            'attachment_form': AttachmentForm(),
        }

        return render(request, 'collab/feature/feature_detail.html', context)


@method_decorator(DECORATORS, name='dispatch')
class FeatureUpdate(SingleObjectMixin, UserPassesTestMixin, View):

    queryset = Feature.objects.all()
    pk_url_kwarg = 'feature_id'
    LinkedFormset = modelformset_factory(
        model=FeatureLink,
        form=FeatureLinkForm,
        extra=0,
        can_delete=True)

    AttachmentFormset = modelformset_factory(
        model=Attachment,
        form=AttachmentForm,
        extra=0,
        can_delete=True)

    def test_func(self):
        user = self.request.user
        feature = self.get_object()
        project = feature.project
        return Authorization.has_permission(user, 'can_update_feature', project, feature)

    def get(self, request, slug, feature_type_slug, feature_id):

        user = request.user
        feature = self.get_object()
        project = feature.project
        feature_type = feature.feature_type

        extra = CustomField.objects.filter(feature_type=feature_type)

        availables_features = Feature.objects.filter(
            project=project,
        ).exclude(feature_id=feature.feature_id)

        feature_form = FeatureBaseForm(
            instance=feature, feature_type=feature_type, user=user)

        extra_form = FeatureExtraForm(feature=feature, extra=extra)

        linked_features = FeatureLink.objects.filter(
            feature_from=feature.feature_id
        ).annotate(
            feature_id=F('feature_to')).values('relation_type', 'feature_id')

        linked_formset = self.LinkedFormset(
            initial=linked_features,
            queryset=FeatureLink.objects.filter(feature_from=feature.feature_id))

        attachements = Attachment.objects.filter(
            project=project, feature_id=feature.feature_id,
            type_objet='feature'
        )
        attachment_formset = self.AttachmentFormset(
            initial=attachements,
            queryset=attachements
        )

        context = {
            'feature': feature,
            'feature_data': feature.custom_fields_as_list,
            'feature_types': FeatureType.objects.filter(project=project),
            'feature_type': feature.feature_type,
            'project': project,
            'permissions': Authorization.all_permissions(user, project, feature),
            'feature_form': feature_form,
            'extra_form': extra_form,
            'availables_features': availables_features,
            'linked_formset': linked_formset,
            'attachment_formset': attachment_formset,
            'attachment_form': AttachmentForm(),
            'comment_form': CommentForm(),
            'attachments': attachements,
            'action': 'update'
        }
        return render(request, 'collab/feature/feature_edit.html', context)

    def post(self, request, slug, feature_type_slug, feature_id):
        user = request.user
        feature = self.get_object()
        project = feature.project
        feature_type = feature.feature_type
        availables_features = Feature.objects.filter(
            project=project,
        ).exclude(feature_id=feature.feature_id)

        extra = CustomField.objects.filter(feature_type=feature_type)
        feature_form = FeatureBaseForm(
            request.POST, instance=feature, feature_type=feature_type, user=user)

        extra_form = FeatureExtraForm(request.POST, feature=feature, extra=extra)

        linked_formset = self.LinkedFormset(request.POST)

        attachment_formset = self.AttachmentFormset(request.POST)

        old_status = feature.status
        old_geom = feature.geom.wkt if feature.geom else ''

        forms_are_valid = feature_form.is_valid() and \
            extra_form.is_valid() and \
            linked_formset.is_valid() and \
            attachment_formset.is_valid()

        if not forms_are_valid:
            messages.error(request, "Erreur à la mise à jour du signalement. ")
            context = {
                'feature': feature,
                'feature_types': FeatureType.objects.filter(project=project),
                'feature_type': feature.feature_type,
                'project': project,
                'permissions': Authorization.all_permissions(user, project),
                'feature_form': feature_form,
                'extra_form': extra_form,
                'availables_features': availables_features,
                'linked_formset': linked_formset,
                'attachment_formset': attachment_formset,
                'attachments': Attachment.objects.filter(
                    project=project, feature_id=feature.feature_id,
                    type_objet='feature'),
                'action': 'update'
            }
            return render(request, 'collab/feature/feature_edit.html', context)
        else:

            updated_feature = feature_form.save(
                project=project,
                feature_type=feature_type,
                extra=extra_form.cleaned_data
            )

            # On contextualise l'evenement en fonction des modifications apportés
            data = {} if not updated_feature.feature_data else updated_feature.feature_data

            data['feature_status'] = {
                'has_changed': (old_status != updated_feature.status),
                'old_status': old_status,
                'new_status': updated_feature.status,
            }
            data['feature_geom'] = {
                'has_changed': (old_geom != updated_feature.geom),
                'old_geom': old_geom,
                'new_geom': updated_feature.geom.wkt if updated_feature.geom else '',
            }

            Event.objects.create(
                feature_id=updated_feature.feature_id,
                event_type='update',
                object_type='feature',
                user=user,
                project_slug=updated_feature.project.slug,
                feature_type_slug=updated_feature.feature_type.slug,
                data=data
            )

            # Traitement des signalements liés
            for data in linked_formset.cleaned_data:
                feature_to = data.get('feature_to')

                if feature_to:
                    if not data.get('DELETE'):
                        FeatureLink.objects.get_or_create(
                            relation_type=data.get('relation_type'),
                            feature_from=feature_id,
                            feature_to=feature_to
                        )
                    if data.get('DELETE'):
                        qs = FeatureLink.objects.filter(
                            relation_type=data.get('relation_type'),
                            feature_from=feature_id,
                            feature_to=feature_to
                        )
                        for instance in qs:
                            instance.delete()

            # Traitement des piéces jointes
            for data in attachment_formset.cleaned_data:
                attachment = data.get('attachment_file')

                if attachment:
                    if not data.get('DELETE'):
                        Attachment.objects.get_or_create(
                            feature_id=feature_id,
                            project=project,
                            author=user,
                            title=data.get('relation_type'),
                            info=data.get('info'),
                            type_objet='feature',
                            attachment_file=data.get('attachment_file'),
                        )
                    if data.get('DELETE'):
                        logger.error('TO BE CONTINUED')

        return redirect(
            'collab:feature_detail', slug=project.slug,
            feature_type_slug=feature_type.slug, feature_id=feature.feature_id)


class FeatureDelete(DeleteView):
    model = Feature
    pk_url_kwarg = 'feature_id'
    success_url = reverse_lazy('collab:index')


######################
# FEATURE TYPE VIEWS #
######################


@method_decorator(DECORATORS, name='dispatch')
class FeatureTypeCreate(SingleObjectMixin, UserPassesTestMixin, View):
    queryset = Project.objects.all()
    CustomFieldsFormSet = modelformset_factory(
        CustomField,
        # can_delete=True,
        # can_order=True,
        form=CustomFieldModelForm,
        formset=CustomFieldModelBaseFS,
        extra=0,
    )

    def test_func(self):
        user = self.request.user
        project = self.get_object()
        return Authorization.has_permission(user, 'can_create_feature_type', project)

    def get(self, request, slug):
        project = self.get_object()
        user = request.user
        form = FeatureTypeModelForm()
        formset = self.CustomFieldsFormSet(queryset=CustomField.objects.none())

        context = {
            'form': form,
            'formset': formset,
            'permissions': Authorization.all_permissions(user, project),
            'feature_types': project.featuretype_set.all(),
            'project': project,
        }
        return render(request, 'collab/feature_type/feature_type_create.html', context)

    def post(self, request, slug):
        user = request.user
        form = FeatureTypeModelForm(request.POST or None)
        project = self.get_object()
        formset = self.CustomFieldsFormSet(request.POST or None)

        if form.is_valid() and formset.is_valid():
            feature_type = form.save(commit=False)
            feature_type.project = project
            feature_type.save()

            for data in formset.cleaned_data:
                if not data.get("DELETE"):
                    CustomField.objects.create(
                        feature_type=feature_type,
                        position=data.get("position"),
                        label=data.get("label"),
                        name=data.get("name"),
                        field_type=data.get("field_type"),
                    )
            return redirect('collab:project', slug=project.slug)
        else:
            context = {
                'form': form,
                'formset': formset,
                'permissions': Authorization.all_permissions(user, project),
                'feature_types': project.featuretype_set.all(),
                'project': project,
            }
            return render(request, 'collab/feature_type/feature_type_create.html', context)


@method_decorator(DECORATORS, name='dispatch')
class FeatureTypeDetail(SingleObjectMixin, UserPassesTestMixin, View):
    queryset = FeatureType.objects.all()
    slug_url_kwarg = 'feature_type_slug'

    def test_func(self):
        user = self.request.user
        feature_type = self.get_object()
        project = feature_type.project
        return Authorization.has_permission(user, 'can_view_feature_type', project)

    def get(self, request, slug, feature_type_slug):
        feature_type = self.get_object()
        project = feature_type.project
        user = request.user
        features = Feature.objects.filter(feature_type=feature_type)
        structure = FeatureTypeSerializer(feature_type, context={'request': request})

        context = {
            'feature_type': feature_type,
            'permissions': Authorization.all_permissions(user, project),
            'feature_types': project.featuretype_set.all(),
            'features': features,
            'project': project,
            'structure': structure.data
        }

        return render(request, 'collab/feature_type/feature_type_detail.html', context)


@method_decorator(DECORATORS, name='dispatch')
class ImportFromGeoJSON(SingleObjectMixin, UserPassesTestMixin, View):
    queryset = FeatureType.objects.all()
    slug_url_kwarg = 'feature_type_slug'

    def test_func(self):
        return True
        user = self.request.user
        feature_type = self.get_object()
        project = feature_type.project
        return Authorization.has_permission(user, 'can_create_feature', project)

    def get_geom(self, geom):

        # Si geoJSON
        if isinstance(geom, dict):
            geom = str(geom)
        try:
            geom = GEOSGeometry(geom, srid=4326)
        except (GEOSException, ValueError):
            geom = None
        return geom

    def create_features(self, request, creator, data, feature_type):
        new_features = data.get('features')
        nb_features = len(new_features)
        for feature in new_features:
            properties = feature.get('properties')

            current = Feature.objects.create(
                title=properties.get('title'),
                description=properties.get('description'),
                status='draft',
                creator=creator,
                project=feature_type.project,
                feature_type=feature_type,
                geom=self.get_geom(feature.get('geometry')),
                feature_data=properties.get('feature_data'),
            )

            simili_features = Feature.objects.filter(
                title=current.title, description=current.description
            ).exclude(feature_id=current.feature_id)

            if simili_features.count() > 0:
                for row in simili_features:
                    FeatureLink.objects.get_or_create(
                        relation_type='doublon',
                        feature_from=current.feature_id,
                        feature_to=row.feature_id
                    )
        if nb_features > 0:
            msg = "{nb} signalement(s) importé(s). ".format(nb=nb_features)
            messages.info(request, msg)

    @transaction.atomic
    def post(self, request, slug, feature_type_slug):
        """
        Import d'un fichier GeoJSON

        Disponibilité de la fonction :
            Fonction disponible pour chaque type de signalement de chaque projet (dans la page descriptive du type de signalements)
            Uniquement pour les utilisateurs qui peuvent créer des signalements (contributeurs et niveaux de droits supérieurs du projet)
        Action de la fonction :
            Créer de nouveaux signalements
            La fonction n'est pas capable de mettre à jour des signalements existants ni de supprimer des signalements existants
            Les enregistrements soupçonnés de former des doublons sont marqués automatiquement à l'aide du mécanisme de relation entre signalements. Les doublons sont identifiés par l'égalité de leur titre et de leur description)
            Les enregistrements importés sont enregistrés avec le statut "brouillon"
        Structuration des données importées :
            Le modèle de données supporté par la fonction d'import doit être décrit dans la page descriptive du type de signalements
            Seuls les champs portant les mêmes noms que la table des signalements en base seront exploités
            Pour les champs correspondant à des listes de choix : seules les valeurs seront exploitées (pas les libellés en langage naturel)
            Géométries supportées pour Excel : colonne geom en WKT dans le système de coordonnées attendu (pas de reprojection - cf. DB_SRID dans settings.py)

        En cas d'erreur lors de l'exécution de l'import un compte rendu des erreurs rencontrées doit être présenté à l'utilisateur.
        Si des signalements ont été créés, le nombre de signalements créés doit être indiqué à l'utilisateur.
        """
        feature_type = self.get_object()
        try:
            up_file = request.FILES['json_file'].read()
            data = json.loads(up_file.decode('utf-8'))
        except Exception:
            logger.exception('ImportFromGeoJSON.post')
            messages.error(request, "Erreur à l'import du fichier. ")
        else:
            try:
                with transaction.atomic():
                    self.create_features(request, request.user, data, feature_type)
            except IntegrityError:
                messages.error(request, "Erreur à lors de l'ajout des signalements. ")

        return redirect('collab:feature_type_detail', slug=slug, feature_type_slug=feature_type_slug)


@method_decorator(DECORATORS, name='dispatch')
class ImportFromImage(SingleObjectMixin, UserPassesTestMixin, View):
    queryset = FeatureType.objects.all()
    slug_url_kwarg = 'feature_type_slug'

    def test_func(self):
        return True
        user = self.request.user
        feature_type = self.get_object()
        project = feature_type.project
        return Authorization.has_permission(user, 'can_create_feature', project)

    def get_geom(self, geom):

        # Si geoJSON
        if isinstance(geom, dict):
            geom = str(geom)
        try:
            geom = GEOSGeometry(geom, srid=4326)
        except (GEOSException, ValueError):
            geom = None
        return geom

    def post(self, request, slug, feature_type_slug):

        context = {}
        try:
            up_file = request.FILES['image_file']
        except Exception:
            logger.exception('ImportFromImage.post')
            context['status'] = "error"
            context['message'] = "Erreur à l'import du fichier. "
            status = 400

        try:
            data_geom_wkt = exif.get_image_geoloc_as_wkt(up_file, with_alt=False, ewkt=False)
        except Exception:
            logger.exception('ImportFromImage.post')
            context['status'] = "error"
            context['message'] = "Erreur lors de la lecture des données GPS. "
            status = 400
        else:
            geom = self.get_geom(data_geom_wkt)
            context['geom'] = geom.wkt
            context['status'] = "success"
            status = 200

        return JsonResponse(context, status=status)


#################
# PROJECT VIEWS #
#################


class ProjectDetail(DetailView):

    model = Project

    # TODO@cbenhabib: renommer en project_detail.html
    template_name = "collab/project/project_home.html"

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)
        project = self.get_object()
        user = self.request.user

        permissions = Authorization.all_permissions(user, project)

        comments = Comment.objects.filter(
            project=project
        ).values(
            'author__first_name', 'author__last_name',
            'comment', 'created_on'
        ).order_by('-created_on')[0:3]

        features = Feature.objects.filter(
            project=project
        )

        serilized_projects = ProjectDetailedSerializer(project)

        context['project'] = serilized_projects.data
        context['user'] = user
        context['comments'] = comments
        context['features'] = features
        context['last_features'] = features.order_by('-created_on')[0:3]
        context['permissions'] = permissions
        context['feature_types'] = project.featuretype_set.all()
        context['is_suscriber'] = Subscription.is_suscriber(user, project)
        return context


@method_decorator(DECORATORS, name='dispatch')
class ProjectUpdate(SingleObjectMixin, UserPassesTestMixin, View):
    queryset = Project.objects.all()

    def test_func(self):
        user = self.request.user
        project = self.get_object()
        return Authorization.has_permission(user, 'can_update_project', project)

    def get(self, request, slug):
        project = get_object_or_404(Project, slug=slug)

        form = ProjectModelForm(instance=project)
        context = {
            'form': form,
            'permissions': Authorization.all_permissions(request.user, project),
            'feature_types': project.featuretype_set.all(),
            'is_suscriber': Subscription.is_suscriber(request.user, project),
            'action': 'update'
        }
        return render(request, 'collab/project/project_edit.html', context)

    def post(self, request, slug):
        project = self.get_object()
        form = ProjectModelForm(request.POST, request.FILES, instance=project)
        if form.is_valid() and form.has_changed():
            form.save()
            return redirect('collab:project', slug=project.slug)

        context = {
            'form': form,
            'feature_types': project.featuretype_set.all()
        }
        return render(request, 'collab/project/project_edit.html', context)


@method_decorator(DECORATORS, name='dispatch')
class ProjectCreate(CreateView):

    model = Project

    form_class = ProjectModelForm

    template_name = 'collab/project/project_edit.html'

    def test_func(self):
        user = self.request.user
        return user.is_superuser or user.is_administrator

    def form_valid(self, form):
        form.instance.creator = self.request.user
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'create'
        return context


@method_decorator(DECORATORS, name='dispatch')
class ProjectMap(SingleObjectMixin, UserPassesTestMixin, View):
    queryset = Project.objects.all()

    def test_func(self):
        user = self.request.user
        project = self.get_object()
        return Authorization.has_permission(user, 'can_update_feature', project)

    def get(self, request, slug):
        # raise NotImplementedError
        return render(request, 'collab/project/project_map.html', {})


@method_decorator(DECORATORS, name='dispatch')
class ProjectMembers(SingleObjectMixin, UserPassesTestMixin, View):
    queryset = Project.objects.all()
    AuthorizationFormSet = modelformset_factory(
        Authorization,
        can_delete=True,
        form=AuthorizationForm,
        formset=ExtendedBaseFS,
        extra=0,
        fields=('first_name', 'last_name', 'username', 'email', 'level'),
    )

    def test_func(self):
        user = self.request.user
        project = self.get_object()
        return Authorization.has_permission(user, 'is_project_administrator', project)

    def get(self, request, slug):
        user = self.request.user
        project = self.get_object()
        formset = self.AuthorizationFormSet(queryset=Authorization.objects.filter(project=project))
        authorised = Authorization.objects.filter(project=project)
        permissions = Authorization.all_permissions(user, project)
        context = {
            "title": "Gestion des membres du projet {}".format(project.title),
            'authorised': authorised,
            'permissions': permissions,
            'formset': formset,
            'feature_types': FeatureType.objects.filter(project=project)
        }

        return render(request, 'collab/project/admin_members.html', context)

    def post(self, request, slug):
        user = self.request.user
        project = self.get_object()
        formset = self.AuthorizationFormSet(request.POST or None)
        authorised = Authorization.objects.filter(project=project)
        permissions = Authorization.all_permissions(user, project)
        if formset.is_valid():

            for data in formset.cleaned_data:
                # id contient l'instance si existante
                authorization = data.pop("id", None)
                if data.get("DELETE"):
                    if authorization:
                        # On ne supprime pas l'utilisateur, mais on cache
                        # ses references dans le signalement
                        if not authorization.user.is_superuser:
                            # hide_feature_user(project, user)
                            pass
                        authorization.delete()
                    else:
                        continue

                elif authorization:
                    authorization.level = data.get('level')
                    authorization.save()
                elif not authorization and not data.get("DELETE"):
                    # On ne crée pas d'utilisateur: il est choisi parmi ceux existants
                    # TODO @cbenhabib: A brancher sur proxy cas
                    try:
                        user = User.objects.get(
                            username=data["username"],
                            email=data["email"],
                            is_active=True
                        )
                    except User.DoesNotExist:
                        messages.error(request, "Aucun utilisateur ne correspond. ")
                    else:
                        Authorization.objects.create(
                            user=user,
                            project=project,
                            level=data.get('level')
                        )

            return redirect('collab:project_members', slug=slug)

        context = {
            "title": "Gestion des membres du projet {}".format(project.title),
            'authorised': authorised,
            'permissions': permissions,
            'formset': formset,
            'feature_types': FeatureType.objects.filter(project=project)
        }
        return render(request, 'collab/project/admin_members.html', context)


######################
# SUBSCRIPTION VIEWS #
######################

@method_decorator(DECORATORS, name='dispatch')
class SubscribingView(SingleObjectMixin, UserPassesTestMixin, View):

    queryset = Project.objects.all()

    def test_func(self):
        user = self.request.user
        project = self.get_object()
        return Authorization.has_permission(user, 'can_view_feature', project)

    def get(self, request, slug, action):
        project = self.get_object()
        user = request.user

        if action.lower() not in ['ajouter', 'annuler']:
            msg = "Erreur de syntaxe dans l'url d'abonnement. "
            logger.error(msg)
            messages.error(request, "Cette action est incorrecte")

        elif action.lower() == 'ajouter':
            obj, _created = Subscription.objects.get_or_create(
                project=project,
            )
            obj.users.add(user)
            obj.save()
            messages.info(request, 'Vous etes bien abonné aux notifcations pour ce projet. ')

        elif action.lower() == 'annuler':
            try:
                obj = Subscription.objects.get(project=project)
                obj.users.remove(user)
                obj.save()
                messages.info(request, 'Vous ne recevrez plus les notifications liés à ce projet. ')
            except Exception:
                logger.exception('SubscribingView.get')

        return redirect('collab:project', slug=slug)
