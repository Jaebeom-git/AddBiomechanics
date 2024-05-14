import React from "react";
import { Outlet, useLocation } from "react-router";
import MocapS3Cursor from '../state/MocapS3Cursor';
import { observer } from "mobx-react-lite";
import { Button } from "react-bootstrap";

type ErrorDisplayProps = {
  cursor: MocapS3Cursor;
};

const ErrorDisplay = observer(({ cursor }: ErrorDisplayProps) => {
  console.log("Rerendering ErrorDisplay");
  let location = useLocation();

  let errorBanner = null;
  if (cursor.hasNetworkErrors() && location.pathname !== '/') {
    let errors: string[] = cursor.getNetworkErrors();
    errorBanner = <>
      <div className="error-backdrop" />
      <div className="error-container">
        <div className="error-banner">
          <h3>Network Problem</h3>
          {errors.map((e) => {
            return <p>{e}</p>;
          })}
          <p>
            Check that your internet connection is stable.
          </p>
          <Button variant="warning" onClick={() => {
            cursor.clearNetworkErrors();
          }}>Acknowledge and Continue</Button>
        </div>
      </div>
    </>;
  }

  return (<>
    {errorBanner}
    <Outlet />
  </>);
});

export default ErrorDisplay;
