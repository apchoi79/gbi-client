function tileUrl(bounds) {
    var tileInfo = this.getTileInfo(bounds.getCenterLonLat());
    return this.url + this.layer + '/' + this.matrixSet + '-' + this.matrix.identifier + '-'
        + tileInfo.col + '-'
        + tileInfo.row + '/tile';
}


OpenLayers.Tile.Image.prototype.onImageError = function() {
        var img = this.imgDiv;
        if (img.src != null) {
            this.imageReloadAttempts++;
            if (this.imageReloadAttempts <= OpenLayers.IMAGE_RELOAD_ATTEMPTS) {
                this.setImgSrc(this.layer.getURL(this.bounds));
            } else {
                OpenLayers.Element.addClass(img, "olImageLoadError");
                this.events.triggerEvent("loaderror");
                img.src = openlayers_blank_image;
                this.onImageLoad();
            }
        }
}

/**
 * style for the vector elemenets
 **/
var sketchSymbolizers = {
  "Point": {
    pointRadius: 8,
    fillColor: "#ccc",
    fillOpacity: 1,
    strokeWidth: 1,
    strokeOpacity: 1,
    strokeColor: "#D6311E"
  },
  "Line": {
    strokeWidth: 3,
    strokeOpacity: 1,
    strokeColor: "#D6311E",
    strokeDashstyle: "dash"
   },
   "Polygon": {
    strokeWidth: 2,
    strokeOpacity: 1,
    strokeColor: "#D6311E",
    fillColor: "#D6311E",
    fillOpacity: 0.6
   }
};

var style = new OpenLayers.Style();
style.addRules([
    new OpenLayers.Rule({symbolizer: sketchSymbolizers})
]);

var styleMap = new OpenLayers.StyleMap(
    {"default": style}
);


/**
 * style for the vector elemenets
 **/
var download_area_symbolizers = {
   "Polygon": {
        strokeWidth: 2,
        strokeOpacity: 1,
        strokeColor: "#24D0D6",
        fillOpacity: 0
   }
};

var download_area_style = new OpenLayers.Style();
download_area_style.addRules([
    new OpenLayers.Rule({symbolizer: download_area_symbolizers})
]);

var download_area_style_map = new OpenLayers.StyleMap(
    {"default": download_area_style}
);



function init_map() {
    OpenLayers.ImgPath = openlayers_image_path;

    var extent = new OpenLayers.Bounds(-20037508.34, -20037508.34,
                                         20037508.34, 20037508.34);
    var numZoomLevels = view_zoom_level_end;

    if (base_layer.restrictedExtent) {
        extent = base_layer.restrictedExtent;
    } else if (base_layer.getMaxExtent()) {
        extent = base_layer.getMaxExtent();
    }

    var options = {
        projection: new OpenLayers.Projection("EPSG:3857"),
        units: "m",
        maxResolution: 156543.0339,
        maxExtent: new OpenLayers.Bounds(-20037508.34, -20037508.34,
                                         20037508.34, 20037508.34),
        numZoomLevels: numZoomLevels,
        controls: [],
        theme: openlayers_theme_url,
        restrictedExtent: extent
    };

    var map = new OpenLayers.Map( 'map', options );

    map.addLayer(basic);
    map.addLayer(base_layer);

    map.addControl(
        new OpenLayers.Control.TouchNavigation({
            dragPanOptions: {
                enableKinetic: true
            }
        })
    );
    var layerswitcher = new OpenLayers.Control.LayerSwitcher({
        roundedCorner: true
    });
    map.addControl(layerswitcher)
    layerswitcher.maximizeControl();

    map.addControl(new OpenLayers.Control.PanZoomBar());
    map.addControl(new OpenLayers.Control.Navigation());
    map.addControl(new OpenLayers.Control.ZoomStatus({
        prefix: '<div id="show_zoomlevel">Level: ',
        suffix: '</div>'
     })
    );
    map.zoomToMaxExtent();

    return map;
}

function activate_draw_controls(map) {
    var draw_type = null;

    var draw_layer = new OpenLayers.Layer.Vector("Draw Layer", {
        displayInLayerSwitcher: false,
        styleMap: styleMap,
        eventListeners: {
            featureadded: function(f) {
                if(!draw_layer.load_active) {
                    f.feature.attributes['type'] = $('.draw_control_element.active')[0].id;
                    toggle_start_button();
                }
                if (!draw_layer.load_active) {
                    get_data_volume();
                }
            },
            featuresadded: function() {
                toggle_start_button();
            },
            featureremoved: function(f) {
                toggle_start_button();
            },
            featuresremoved: function() {
                get_data_volume();
                toggle_start_button();
            },
            beforefeaturemodified: function(f) {
                if(f.feature.attributes['type'] == BOX_CONTROL) {
                    draw_controls[MODIFY_CONTROL].mode = OpenLayers.Control.ModifyFeature.DRAG;
                    draw_controls[MODIFY_CONTROL].mode |= OpenLayers.Control.ModifyFeature.RESIZE;
                    draw_controls[MODIFY_CONTROL].mode |= OpenLayers.Control.ModifyFeature.RESHAPE;
                } else {
                   draw_controls[MODIFY_CONTROL].mode = OpenLayers.Control.ModifyFeature.DRAG
                   draw_controls[MODIFY_CONTROL].mode |= OpenLayers.Control.ModifyFeature.RESHAPE;
                }
            },
            afterfeaturemodified: function() {
                get_data_volume()
            }

    }});

    draw_layer.load_active = false;
    map.addLayer(draw_layer);

    draw_controls = {};
    draw_controls[MULTIPOLYGON_CONTROL] = new OpenLayers.Control.DrawFeature(draw_layer, OpenLayers.Handler.Polygon, {
        handlerOptions: {
            layerOptions: {styleMap: styleMap},
            holeModifier: "altKey"
    }});

    draw_controls[BOX_CONTROL] = new OpenLayers.Control.DrawFeature(draw_layer, OpenLayers.Handler.RegularPolygon, {
        handlerOptions: {
            sides: 4,
            irregular: true
    }});

    draw_controls[MODIFY_CONTROL] = new OpenLayers.Control.ModifyFeature(draw_layer);

    $.each(draw_controls, function(name, control) {
        map.addControl(control);
    });
    $('.draw_control_element').click(toggle_draw_control);
    $('#'+DELETE_FEATURE).click(delete_selected_feature);
    $('#'+DELETE_ALL_FEATURES).click(delete_all_features);

    return draw_layer;
}

function toggle_draw_control() {
    if(!draw_controls) return false;
    elem = this;
    draw_control_elements = $('.draw_control_element');
    draw_control_elements.each(function(idx, el) {
        var el_id = el.id;
        el = $(el);
        if(el.hasClass('active')) {
            el.toggleClass('active');
            draw_controls[el_id].deactivate();
            if (el_id == MODIFY_CONTROL) {
                $('#'+DELETE_FEATURE).toggleClass('active').attr('disabled', 'disabled')
            }
        } else if (el_id == elem.id) {
            el.toggleClass('active');
            draw_controls[el_id].activate();
            if (el_id == MODIFY_CONTROL) {
                $('#'+DELETE_FEATURE).toggleClass('active').removeAttr('disabled')
            }
        }
    });
    return false;
}

function delete_selected_feature() {
    // save selecte features before modify control is deactive
    var selected_features = draw_layer.selectedFeatures[0];
    if (selected_features) {
        draw_controls[MODIFY_CONTROL].deactivate();
        draw_layer.removeFeatures(selected_features)
        draw_controls[MODIFY_CONTROL].activate();
    }
    return false;
}

function delete_all_features() {
    draw_layer.removeAllFeatures();
    get_data_volume();
    return false;
}

function save_features(target_url) {
    var parser = new OpenLayers.Format.GeoJSON();
    geojson = parser.write(draw_layer.features);
    $.ajax({
        type: 'POST',
        url: target_url,
        data: {'feature_collection': geojson},
        success: function() {
            $('#output').text(geojson);
        },
        dataType: 'json'
    });
}

function load_features(data) {
    draw_layer.load_active = true;
    var parser = new OpenLayers.Format.GeoJSON();
    if (jQuery.isArray(data)) {
       $.each(data, function(index, geom) {
            draw_layer.addFeatures(parser.read(geom.geometry));
       });
    } else {
        feature_collection = parser.read(data);
        if (feature_collection)
            draw_layer.addFeatures(feature_collection);
    }
    if (draw_layer.features.length > 0) {
        draw_layer.map.zoomToExtent(draw_layer.getDataExtent());
    }
    get_data_volume();
    draw_layer.load_active = false;
}