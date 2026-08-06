import React from 'react';
import { Card, ListGroup, Badge, Col, Row } from 'react-bootstrap';

const AllGamesSuggestionsPanel = ({ suggestions }) => {
  if (!suggestions || Object.keys(suggestions).length === 0) {
    return null;
  }

  return (
    <Col md={12} className="mb-3">
      <Card className="neon-border">
        <Card.Body>
          <Card.Title className="text-white">
            <i className="bi bi-lightbulb-fill me-2"></i>All Game Suggestions
          </Card.Title>
          <ListGroup variant="flush">
            {Object.entries(suggestions).map(([gameName, suggestion]) => (
              <ListGroup.Item key={gameName} className="bg-dark text-white border-secondary">
                <Row className="align-items-center">
                  <Col xs={4}>
                    <h6 className="mb-0">{gameName.toUpperCase()}</h6>
                  </Col>
                  <Col xs={8} className="text-end">
                    {suggestion.predicted_numbers && suggestion.predicted_numbers.length > 0 ? (
                      suggestion.predicted_numbers.map((num, idx) => (
                        <Badge key={idx} bg="info" className="me-1 mb-1">
                          {num}
                        </Badge>
                      ))
                    ) : (
                      <Badge bg="warning">No prediction</Badge>
                    )}
                    {suggestion.message && suggestion.message !== "Prediction successful" && (
                      <small className="text-danger ms-2">{suggestion.message}</small>
                    )}
                  </Col>
                </Row>
              </ListGroup.Item>
            ))}
          </ListGroup>
        </Card.Body>
      </Card>
    </Col>
  );
};

export default AllGamesSuggestionsPanel;